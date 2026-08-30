# Career Strategy: Policy Brief vs. JMP Focus

Context: Cornell applied econ PhD, JMP on coal mining × drinking water quality.
No other publications or internships. Summer 2026 to work on JMP. Target: economic
consulting (Analysis Group, Cornerstone, Brattle, CRA, Compass Lexecon, NERA).
Going on the market fall 2026.

Question: Would writing a short 5–10 page policy brief help? If so, on what topic?

---

## Short answer

A policy brief can help — but it's a *secondary* lever. For economic consulting,
the things that actually move hiring decisions, roughly in order:

1. **The job market paper itself** — single biggest signal; doubles as your writing
   sample.
2. **Networking and timing** — these firms recruit early, through referrals/conferences.
3. **Interview readiness** — case-style reasoning, "explain your research to a smart
   non-economist," brainteasers.
4. **Demonstrated communication of technical work to non-technical audiences** ←
   *this* is where a brief helps.

A separate 5–10 page brief on a *new* topic is probably **not** the best use of the
summer. It splits focus away from the JMP during the one summer you have to finish
it, and firms won't weigh a standalone brief anywhere near the JMP.

---

## What I'd actually recommend

**Don't start a new research thread. Repackage the work you already have.** Write
your "brief" *on your own coal-mining/drinking-water paper.* Three wins at once:
polished writing sample, sharper JMP framing, networking artifact — near-zero added
research cost.

Concretely, produce a **non-technical 4–6 page policy brief version of the JMP**:
- The question and why a policymaker/regulator should care (ARP, Safe Drinking Water
  Act enforcement, environmental-justice angle).
- One clean headline result and one clear figure (already generated in `didhet.r` /
  `output/fig/`).
- The identification logic in plain English (high-sulfur watersheds, ARP Phase I
  shock) — *no equations*.
- A concrete policy implication: e.g., monitoring/enforcement targeting in
  mining-adjacent CWSs, or the MR-vs-MCL strategic-substitution finding (a compelling
  regulatory story).

The "translate a 2SLS paper into a one-figure argument a regulator understands"
exercise *is* the core consulting skill — exactly what they screen for.

---

## Why this beats a brand-new brief

- **Time.** It's June 2026; if going on the market this fall, the summer is for
  *finishing and polishing the JMP*, not opening a new front.
- **Marginal signal.** A new-topic brief signals "I can write." A non-technical
  version of your own complex IV paper signals "I can take rigorous empirical work
  and make it land with a decision-maker" — which is the job.
- **Compounding.** The exec-summary/intro you write transfers directly into your
  job-market materials and your talk.

---

## Higher-priority summer moves

1. **Make the JMP excellent and finished enough to present** — clean identification
   story, one memorable headline number, robust placebo/falsification tests. The repo
   already has the placebo structure (coliform/VOC/SOC) and first-stage F checks;
   lean into them — exactly what interviewers probe.
2. **Build a 5-minute and a 20-minute "walk me through your research" pitch.**
   Practice on non-economists.
3. **Network now.** Reach out to PhD-econ alumni at these firms (Cornell has many).
   Summer coffee chats → fall referrals. Higher ROI than any writing.
4. **Polish a non-technical exec summary** (the "brief" above) to attach to
   applications / send to contacts.
5. **Have one or two clean code samples ready** — R/Python both look good; consulting
   values the SQL/data-wrangling competence the pipeline already shows.

---

## If you still want a standalone brief

Make it **adjacent to the JMP so it reinforces your expertise**:
- *"Who bears the drinking-water cost of coal's decline?"* — distributional /
  environmental-justice framing of the results.
- *"Strategic monitoring violations: how water systems game SDWA enforcement"* — the
  MR↔MCL substitution finding stands alone as a sharp regulatory-design piece, the
  kind of clever institutional insight interviews reward.

Either way: same topic, leverage existing work, no new data build.
