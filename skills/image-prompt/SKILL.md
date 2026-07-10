# Image Prompt — Skill Specification

**Version:** 1.0  
**Last modified:** 2026-05-29

---

## Purpose

Given a piece of written content, this skill:

1. Analyses the content to determine whether a real/personal photo, a stock photo, or an AI-generated image would best serve it.
2. States the recommendation and the reason for it in one sentence.
3. Asks whether to proceed with the recommendation or switch to an AI-generated prompt instead.
4. Delivers the appropriate output for the chosen path.

---

## Inputs

| Input | Required | Default | Notes |
|-------|----------|---------|-------|
| Content | Yes | — | File path or inline text |
| Platform | No | linkedin | linkedin, twitter, instagram-square, instagram-portrait, blog |
| Style override | No | photorealistic | If specified by the user, always honour it |

---

## Decision Criteria

Read the content carefully. Apply the criteria below in order. The first match wins.

### Recommend a real/personal photo when:
- The post is autobiographical or told in personal voice ("I", "my", "we" in experiential context)
- It references a specific personal event, achievement, place, or experience the author was part of
- The post's credibility depends on authenticity — a lived experience, a personal reflection
- A real photo of the author in context would strengthen the message more than any generated image

### Recommend a stock photo when:
- The topic is professional or informational with a human, team, or relatable workplace angle
- Faces or people are needed to create warmth, but it is not a personal story
- The content is about collaboration, career, business, or the human side of work
- AI art would feel too abstract, cold, or obviously artificial for the tone of the piece

### Recommend an AI-generated image when:
- The topic is conceptual, abstract, or technical (trends, frameworks, ideas, data, technology)
- No natural real-world photograph would capture the subject well
- A specific visual metaphor, mood, or composition is needed that a stock photo cannot provide
- The content is about digital, virtual, or intangible subjects
- Strong control over colour, composition, and atmosphere is more valuable than realism of subject

**Default tie-breaker:** When in doubt between stock and AI-generated, prefer AI-generated. When in doubt between AI-generated and a real photo, prefer real photo.

---

## Conversation Flow

1. State the recommendation in one sentence: which type of image, and why.
2. Ask: *"Would you like to go with that, or would you prefer an AI-generated image prompt instead?"*
3. Wait for the response, then deliver the output for the chosen path (see Output Formats below).

**Shortcut:** If the user has already expressed a preference (e.g. "give me an AI prompt" or "I want a stock photo"), skip directly to the relevant output. Do not make them repeat themselves.

---

## Output Formats

### Path A — Real / Personal Photo

Deliver all four of the following:

1. **Shot description** — What kind of photo would work. Be specific: subject, setting, angle, mood. One short paragraph.
2. **What to look for** — Concrete guidance for searching the user's own photo library. What signals "this is the one".
3. **What to avoid** — Two or three things that would undermine the post (posed shots, distracting backgrounds, poor lighting, etc.).
4. **Stock fallback** — In case no suitable personal photo exists: 2–3 search terms and which site to try (Unsplash for editorial quality; Pexels for variety).

---

### Path B — Stock Photo

Deliver all four of the following:

1. **Search terms** — 3–5 specific terms. Specific enough to narrow results; not so narrow that nothing comes up.
2. **What to look for** — Brief description of what a good result looks like (lighting, mood, subject).
3. **What to avoid** — Two or three things to rule out (generic handshakes, over-lit studio smiles, clipart-style illustrations, obvious AI faces).
4. **Recommended site** — Unsplash or Pexels (or both, with a note on the difference).

---

### Path C — AI-Generated Image Prompt

Deliver a single, complete prompt using the structure below. Write it as one cohesive paragraph, not a bullet list — image generators respond better to flowing prompts than to fragmented instructions.

**Prompt structure (in order):**

```
[ART STYLE]. [SUBJECT / SCENE]. [LIGHTING]. [MOOD]. [COLOUR PALETTE]. [COMPOSITION]. [CAMERA / LENS if photorealistic]. [EXCLUSIONS]. [ASPECT RATIO].
```

**Guidance for each element:**

**Art style**
- Default: photorealistic.
- Override when the content clearly calls for it:
  - *Editorial illustration* — opinion pieces, think-pieces, personal essays
  - *Abstract / geometric* — highly technical, data-driven, or systems-level content
  - *Flat design / vector* — light-hearted, instructional, or beginner-facing content
  - *Cinematic digital art* — future-looking, dramatic, or aspirational content
- If overriding the default, note the reason briefly after delivering the prompt.

**Subject / scene**
- Derive directly from the main theme of the content. Be specific and visual — avoid vague nouns like "technology" or "the future".
- Translate abstract topics into concrete visual metaphors. Examples:
  - "TypeScript dominance" → "a single luminous blueprint spreading across a dark surface, drawing fragmented code particles into alignment"
  - "Edge computing" → "a city skyline at dusk with glowing data streams radiating outward from multiple rooftop nodes"
  - "AI as a pair programmer" → "two translucent figures side by side at a workstation, one human and one made of light, both focused on the same screen"

**Lighting**
- Specify type and direction: natural window light, soft overhead studio, neon underlighting, golden hour, atmospheric glow, dramatic side-lighting, etc.
- Lighting carries mood — choose it deliberately.

**Mood**
- One to three words: "precise and forward-looking", "warm and collaborative", "calm authority", "energetic and bold", etc.

**Colour palette**
- Be specific. "Cool blues and electric teal with white highlights" is useful. "Blue and white" is not.
- Three colours or a tight range is better than a vague adjective.

**Composition**
- Specify framing relative to the platform's aspect ratio (see Platform Specifications below).
- For LinkedIn: if the user might want to add a text overlay, note that the left or right third should be intentionally darker or emptier to accommodate it.
- Call out focal point, depth of field, or any meaningful negative space.

**Camera / lens (photorealistic only)**
- Anchor the realism with a specific camera body and lens. Examples:
  - Sony A7R IV with 85mm f/1.4 — warm, shallow focus portraiture
  - Canon EOS R5 with 24mm f/2.8 — wider, environmental
  - Nikon Z9 with 50mm f/2 — versatile, neutral
  - Fujifilm X-T5 with 35mm f/1.4 — slightly filmic, intimate

**Exclusions**
- Always include: *No text, no watermarks.*
- Add *no human faces* when faces would distract from an abstract or conceptual subject.
- Add *no UI screenshots, no clipart, no stock-photo clichés* where relevant.

**Aspect ratio**
- Always end with the platform's native dimensions (see table below).

---

## Platform Specifications

| Platform | Dimensions | Aspect Ratio | Notes |
|----------|------------|--------------|-------|
| linkedin | 1200×627px | 1.91:1 | Landscape; leave space for optional text overlay |
| twitter | 1600×900px | 16:9 | Landscape |
| instagram-square | 1080×1080px | 1:1 | Square |
| instagram-portrait | 1080×1350px | 4:5 | Portrait |
| blog | 1600×840px | ~1.9:1 | Landscape; similar to LinkedIn |

---

## Complete Example

**Content:** A LinkedIn post by a front-end developer about the top trends shaping front-end development in 2026 — AI as a pair programmer, TypeScript dominance, edge-first architecture, CSS renaissance, faster build tools.

**Decision:** AI-generated — the topic is conceptual and technical; no real photograph would capture "front-end trends" better than a purpose-built visual metaphor. Photorealistic default applies.

**Recommendation statement:**
> "For this post, I'd recommend an AI-generated image — the content is conceptual and technical, and a generated image can create a visual metaphor that no stock photo could match. Want to go with that, or would you prefer stock photo search terms instead?"

**Prompt:**
> Photorealistic. A close-up of a dark circuit board whose copper traces gradually dissolve at the edges into clean, luminous lines of floating TypeScript code, the two elements merging at the centre into a single coherent structure. Soft blue atmospheric glow emanating from beneath the board, catching the edges of the code fragments in teal light. Precise and forward-looking. Deep navy background with electric teal and crisp white highlights; no warm tones. Wide landscape composition; right third intentionally darker and uncluttered to allow a text overlay. Shot on Sony A7R IV with 50mm f/2 lens, shallow depth of field with sharp focus at the centre merge point. No text, no watermarks, no human faces. 1200×627px.

---

## Style Default Note

The default image style is **photorealistic**. Honour this unless the content makes another style clearly more appropriate — in which case, use the better-suited style and explain the choice in one sentence after delivering the prompt.

---

## Verification

The skill produced a correct result when both hold:

1. It states exactly one recommendation (real photo, stock, or AI-generated) with a one-sentence reason.
2. It delivers the full format for the chosen path with every required element present: Path A and Path B deliver all four listed items; a Path C prompt ends with the platform's native aspect ratio and includes the "No text, no watermarks" exclusion.

A failed check looks like a recommendation with no reason, a Path C prompt missing the aspect ratio or the exclusions line, or a deliverable that skips one of its required items. If the content cannot be classified against the decision criteria, say so and ask rather than guessing.

---

## Hardening
Safety envelope for this skill. All five fields are required.

- **Allowed tool intent:** Read-only access to the supplied content (inline text, or reading a file when a path is given) and text generation only. No network access.
- **Never:** Fetch or generate an actual image, call an external image API, or write any file. The skill produces prompt text and search guidance, not images.
- **Approval-gated:** None. The output is text advice with no side effects.
- **Write boundaries:** None. The skill writes no files; its output is returned in the conversation.
- **Verification / escape hatch:** A reviewer confirms the output names a type, gives a reason, and matches the chosen path's format (see Verification). If no path fits the content, the skill states that and asks rather than inventing a recommendation.
