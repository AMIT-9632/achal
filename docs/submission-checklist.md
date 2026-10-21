# HSC-27 submission checklist (deadline 27 Oct 2026)

From the HSC-27 guidelines (`/mnt/project-files/knowledge/HSC-27-Competition-Guidelines.pdf`,
pages 7–10 and Annexure 1). The Team Lead uploads everything on the MY Bharat portal.

## Files

| # | Item | Format and limit | File name | Owner | Status |
| --- | --- | --- | --- | --- | --- |
| 1 | Presentation deck, 6–7 slides, standard IIT Bombay template | PPT or PDF, ≤ 10 MB | `Jharkhand_ACHAL_Presentation.pdf` | Impact | draft |
| 2 | Demo video, 3–5 min (strict) | MP4/MOV, ≥ 720p, ≤ 80 MB | `Jharkhand_ACHAL_DemoVideo.mp4` | Impact | script ready (`pitch/video-script.md`) |
| 3 | Public code repository | GitHub/GitLab link: source, README with setup, architecture, sample data/tests, licence | — | Lead | waiting for GitHub to be connected |
| 4 | Annexure 1: team details, signed self-certification, self-attested student IDs, govt photo ID details | PDF/Doc, ≤ 5 MB | `Jharkhand_ACHAL_Annexure1.pdf` | Lead | to fill |

Naming rule: `<State>_<TeamName>_<DocumentName>.pdf` for every uploaded document.

## Repository must show (guideline 3)

- [x] Complete source code, MIT licence (`LICENSE`)
- [x] README with setup instructions (`README.md`)
- [x] Architecture documentation (`docs/architecture.md`, `docs/adr/`)
- [ ] Sample data / test cases (`data/samples/` from W1, tests in every service): confirm samples are committed and < 5 MB
- [x] Licence information for dependencies (`docs/ip-register.md`)
- [ ] Repo is **public** before submission; link opens without login
- [ ] SPDX MIT header on every source file (run the check below)

## Annexure 1 (fill by hand, scan, merge into one PDF)

- [ ] A. Team ID (from MY Bharat), Team Name: ACHAL, State: Jharkhand, Theme: 7 – AI, GIS & Emerging
      Technologies; problem: mine subsidence early warning for Jharia coalfield
- [ ] B. For each member: full name, institution (NIT Jamshedpur, its AISHE code), course and year,
      student ID number, date of birth / age on 17 Aug 2026 (18–29), email, government photo ID type
      and number, signature
- [ ] Self-attested copy of each member's student ID card attached
- [ ] C. Self-certification signed and dated by all members
- [ ] D. Team Lead certification, place and date
- [ ] Every member registered individually on MY Bharat; team of at most 3
- [ ] Guideline preference: at least one female member and one member from a non-technology background

## Eligibility quick check (guidelines p. 10)

- [ ] All members Indian citizens, enrolled in an AISHE-registered institution, aged 18–29 on 17 Aug 2026
- [ ] No member was an HSC 2026 national finalist/winner
- [ ] Submission made by the Team Lead before the deadline; the last version uploaded counts
- [ ] Consent to showcase at VBYLD 2027 (Annexure 1, item C5)

## Content rules

- [ ] AI-use disclosed (`docs/ai-use.md`, last slide of the deck, video credits)
- [ ] No third-party images without rights; every map shows "© OpenStreetMap contributors"
- [ ] Every KPI quoted in deck, video and README matches `docs/kpis.md`; simulated data is labelled synthetic
- [ ] Santali text labelled as draft until a native speaker has reviewed it

SPDX header check:

```bash
git ls-files '*.py' '*.ts' '*.tsx' '*.c' '*.cpp' '*.h' '*.sql' '*.yml' | xargs grep -L "SPDX-License-Identifier" 
```
