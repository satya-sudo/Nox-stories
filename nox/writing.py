from __future__ import annotations

import json
import re

from .files import Checkpoints, atomic_write
from .models import Aside, Brief, Framing, Outline, Profile, Review


def words(text):
    return re.findall(r"\b[\w'-]+\b", text)


def sentences(text):
    return [" ".join(words(s.casefold())) for s in re.split(r"[.!?]+", text) if len(words(s)) >= 8]


def remove_repeated_narration(text, previous):
    """Remove exact normalized repeats; leave quoted speech for editorial repair."""
    seen, kept, removed = set(sentences(previous)), [], []
    quote = None
    for match in re.finditer(r"[^.!?]+(?:[.!?]+|$)", text):
        piece = match.group()
        normalized = " ".join(words(piece.casefold()))
        quoted = quote is not None
        for offset, char in enumerate(piece):
            if quote is not None and char == quote:
                quote, quoted = None, True
            elif quote is None and char in ('"', '“', '‘'):
                quote = {'"': '"', '“': '”', '‘': '’'}[char]
                quoted = True
            elif quote is None and char == "'" and (offset == 0 or not piece[offset - 1].isalnum()):
                quote, quoted = "'", True
        if len(words(piece)) >= 8 and normalized in seen and not quoted:
            removed.append(piece.strip())
        else:
            kept.append(piece)
        if len(words(piece)) >= 8:
            seen.add(normalized)
    return "".join(kept).strip(), removed


def scene_issues(text, previous, target):
    issues = []
    count = len(words(text))
    if not int(target * 0.84) <= count <= int(target * 1.16):
        issues.append(f"Scene has {count} words; rewrite near {target} words")
    if re.search(r"\bNox\b|(?im:^\s*(?:narrator|aside|narration|scene\s+\d+|introduction|outro)\s*[:.])", text):
        issues.append("Remove narrator names, speaker labels, and scene headings from story prose")
    if any(marker in text for marker in ("```", "[INSERT", "TODO", "<think>")):
        issues.append("Remove placeholders, reasoning, or production markup")
    if re.search(r"[{}✅🚫]|end_state|world_rules|third.person past tense|all fixed facts used", text, re.I):
        issues.append("Remove schema fragments and compliance notes; keep only story prose")
    if re.search(r"['\"]\s*,\s*(?:\n|$)", text):
        issues.append("Remove stranded serialization punctuation at paragraph endings")
    if len(re.findall(r"\bdidn['’]t\b", text, re.I)) >= 5:
        issues.append("Replace repeated descriptions of what characters did not do with concrete actions and choices")
    if any(len(words(p)) > 220 for p in text.split("\n\n")):
        issues.append("Use 4–6 short paragraphs instead of one long block")
    for refrain in ("held its breath", "world listens", "stories never truly end", "stories? they never truly end"):
        if refrain in text.casefold():
            issues.append("Remove the recycled narrator refrain: " + refrain)
    seen = set(sentences(previous))
    for sentence in sentences(text):
        if sentence in seen:
            issues.append("A sentence repeats earlier narration: " + sentence[:130])
            break
        seen.add(sentence)
    return issues


class Writer:
    def __init__(self, store, episode_id, spec, client):
        self.store, self.id, self.spec, self.client = store, episode_id, spec, client
        self.folder = store.folder(episode_id)
        self.cache = Checkpoints(self.folder / "writing")
        self.brief = Brief.model_validate(spec["brief"])
        self.profile = Profile.model_validate(spec["profile"])

    def event(self, kind, **detail):
        self.store.event(self.id, kind, detail)

    def outline(self):
        inputs = {"spec": self.spec, "step": "outline"}
        cached = self.cache.load("outline", inputs)
        if cached:
            return Outline.model_validate(cached)
        plan = self.client.structured(Outline,
            "Plan coherent original stories. Return only the required JSON. Use concrete actions and a complete ending. Keep fields concise. Nox is the host outside the plot; never a story character.",
            f"Brief: {self.brief.model_dump_json()}\nCreate exactly {self.brief.scene_count} numbered scenes. "
            "Use 2–4 named characters with fixed roles and facts, including each character's pronouns and physical form. Specify each scene's location, goal, 2–4 short action beats, start/end state, a concrete obstacle, a consequential decision, and whether it is a climax. "
            "Every scene must move the plot through a choice and a visible change. The initial problem and decision to help occur in scene 1. Do not allocate a whole scene to watching, waking, standing still or explaining. "
            "Each start state must follow the preceding end state. Put discoveries in the correct order. Do not revive dead characters, change roles, or solve a problem before its cause. "
            "Resolve the central conflict in the penultimate scene; reserve the final scene for consequences and emotional closure. Keep the entire JSON under 1600 words.",
            tokens=2600, seed=self.brief.seed, temperature=0.4)
        if len(plan.scenes) != self.brief.scene_count:
            raise RuntimeError("Outline scene count differs from the brief")
        self.cache.save("outline", inputs, plan.model_dump())
        self.event("outline_saved", title=plan.title)
        return plan

    def write(self, factor=1.0):
        plan = self.outline()
        accepted, summaries = [], []
        target = round((self.brief.target_minutes * self.brief.words_per_minute * factor - 80) / len(plan.scenes))
        canon = {"characters": [c.model_dump() for c in plan.characters], "world_rules": plan.world_rules}
        for index, scene in enumerate(plan.scenes):
            self.store.check_control(self.id)
            # Only planned setup scenes can contain a host aside; the ending stays sincere.
            humor = index in (0, 2) and not scene.climax and index < len(plan.scenes) - 2
            if self.brief.genre == "horror":
                humor = humor and index == 0
            previous = "\n\n".join(accepted)
            inputs = {"spec": self.spec, "scene": scene.model_dump(), "canon": canon,
                      "previous": previous, "summary": summaries[-1] if summaries else "The story has not begun.",
                      "target": target, "humor": humor}
            cached = self.cache.load(f"scene-{index+1:02d}", inputs)
            if cached:
                accepted.append(cached["text"])
                summaries.append(cached["summary"])
                self.event("scene_reused", scene=index + 1)
                continue
            issues, rejected_draft = [], ""
            # A resume can repair a final failed draft without regenerating good prose.
            last_draft = self.cache.load(f"draft-{index+1:02d}-3", inputs)
            recoverable = (last_draft and last_draft.get("issues") and
                           all(issue.startswith("A sentence repeats earlier narration:")
                               for issue in last_draft["issues"]))
            for attempt in range(3):
                self.event("scene_writing", scene=index + 1, attempt=attempt + 1, target_words=target)
                narration = last_draft["text"] if attempt == 0 and recoverable else self.client.text(self.profile.system_prompt,
                    f"Genre voice: {self.profile.genre_rules[self.brief.genre]}\n"
                    f"Fixed facts: {json.dumps(canon)}\nCurrent scene ONLY: {scene.model_dump_json()}\n"
                    f"What already happened: {inputs['summary']}\n"
                    f"Previous final sentence: {accepted[-1].rsplit('.', 2)[-2] if accepted and '.' in accepted[-1] else ''}\n"
                    f"Write about {target} words in four paragraphs of about {target//4} words each. Use third-person past tense. "
                    "Paragraph 1: the current practical problem. Paragraph 2: a concrete attempt with character dialogue. "
                    "Paragraph 3: the obstacle and a consequential choice. Paragraph 4: the visible result, ending at the required end_state. "
                    "Keep the fixed cast, roles, object ownership and timeline. Use everyday verbs, varied sentences, physical details and natural dialogue. "
                    "This is a story scene, not host framing. "
                    "Return only the prose. Host asides will be added separately. " +
                    ("Revise the following draft to fix ONLY these issues, preserving correct facts and beats: " + json.dumps(issues) + "\nDRAFT TO REVISE:\n" + rejected_draft if issues else ""),
                    tokens=1400, seed=self.brief.seed + index * 10 + attempt, temperature=0.65)
                raw_text = narration.strip().replace("*", "")
                text, removed = remove_repeated_narration(raw_text, previous)
                if removed:
                    self.event("scene_repetition_repaired", scene=index + 1,
                               removed=removed, repair_version="exact-narration-v1")
                issues = scene_issues(text, previous, target)
                if not issues:
                    review = self.client.structured(Review,
                        "You are a continuity editor, not a prose stylist. Check each named character's pronouns, species, physical form, role and abilities against immutable facts. Check whether the central problem is resolved before its planned scene or resolved repeatedly. Reject concrete contradictions, missing required beats, premature discoveries, duplicated narration, or an incomplete final resolution. Quote evidence for each issue. Do not invent issues. An approval has an empty issues list. Summarize only events actually established in this scene.",
                        f"Immutable facts: {json.dumps(canon)}\nPrevious state: {inputs['summary']}\n"
                        f"Required current scene: {scene.model_dump_json()}\n"
                        f"Reserved for later scenes: {json.dumps([s.goal for s in plan.scenes[index+1:]])}\n"
                        f"Narration to evaluate:\n{text}\nReturn approved, specific issues, and a factual continuity_summary of at most 70 words.",
                        tokens=450, seed=self.brief.seed + index, temperature=0)
                    issues = review.issues
                self.cache.save(f"draft-{index+1:02d}-{attempt+1}", inputs,
                                {"text": text, "raw_text": raw_text, "removed_sentences": removed,
                                 "repair_version": "exact-narration-v1", "issues": issues})
                if not issues:
                    if humor:
                        aside = self.client.structured(Aside, self.profile.system_prompt,
                            "Write one optional dry host observation reacting to this opening paragraph. "
                            "Maximum 20 words. Return an empty text if no natural joke fits. No labels, no names, no copied catchphrases. " + text.split("\n\n")[0],
                            tokens=80, seed=self.brief.seed + index, temperature=0.6).text.strip()
                        aside = re.sub(r"(?i)^\s*(?:aside|narrator)\s*:\s*", "", aside)
                        if aside and len(words(aside)) <= 25 and not scene_issues(text + "\n\n" + aside, previous, target):
                            paragraphs = text.split("\n\n")
                            paragraphs.insert(1, aside)
                            text = "\n\n".join(paragraphs)
                    record = {"text": text, "summary": review.continuity_summary, "review": review.model_dump(),
                              "repair_version": "exact-narration-v1", "removed_sentences": removed}
                    self.cache.save(f"scene-{index+1:02d}", inputs, record)
                    accepted.append(text)
                    summaries.append(review.continuity_summary)
                    self.event("scene_saved", scene=index + 1, words=len(words(text)))
                    break
                self.event("scene_rejected", scene=index + 1, issues=issues)
                rejected_draft = text
            else:
                raise RuntimeError(f"Scene {index+1} needs editorial attention after three drafts: {issues}")
        framing_inputs = {"spec": self.spec, "premise": plan.premise, "title": plan.title, "framing_revision": 2}
        framing = self.cache.load("framing", framing_inputs)
        if framing is None:
            corrections = ""
            for attempt in range(3):
                result = self.client.structured(Framing, self.profile.system_prompt,
                    f"Genre: {self.brief.genre}. Story premise: {plan.premise}. Title: {plan.title}. "
                    "Write a 30–55 word host introduction: introduce Nox once and include at most one specific dry joke. "
                    "Write a sincere 15–30 word closing, no joke, no moral, no sequel hook, and no repeat of the name Nox. "
                    "No story spoilers, labels, stage directions or copied catchphrases. Also write a short YouTube description identifying this as an original fictional narrated story, and relevant tags." + corrections,
                    tokens=500, seed=self.brief.seed + attempt, temperature=0.6)
                problems = []
                if len(re.findall(r"\bNox\b", result.introduction)) != 1:
                    problems.append("Begin the introduction exactly: Evening. I'm Nox. The host is Nox; introduce him exactly once.")
                if len(words(result.introduction)) > 70 or len(words(result.closing)) > 45:
                    problems.append("Shorten the introduction below 70 words and closing below 45 words.")
                if "Nox" in result.closing:
                    problems.append("Do not reintroduce Nox in the closing.")
                if not problems:
                    framing = result.model_dump()
                    break
                self.event("framing_rejected", attempt=attempt + 1, issues=problems)
                corrections = "\nCorrections: " + " ".join(problems) + " The channel is Nox Stories. Do not invent another show name. Prefer a brief wry observation over a question-and-punchline joke."
            else:
                raise RuntimeError("Narrator framing failed checks after three attempts")
            self.cache.save("framing", framing_inputs, framing)
        units = [{"section": "intro", "text": framing["introduction"]}]
        units += [{"section": f"scene-{i+1:02d}", "text": text} for i, text in enumerate(accepted)]
        units.append({"section": "closing", "text": framing["closing"]})
        script = "\n\n".join(unit["text"] for unit in units)
        atomic_write(self.folder / "script.txt", script)
        self.event("script_ready", words=len(words(script)), title=plan.title)
        return {"title": plan.title, "units": units, "framing": framing, "factor": factor, "framing_revision": 2}
