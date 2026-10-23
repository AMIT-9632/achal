# Translations and review

| Language | Script | Dashboard | Alert SMS | Status |
| --- | --- | --- | --- | --- |
| English | Latin | complete | complete | reference |
| Hindi | Devanagari | complete | complete | written by the team, needs a second reader |
| Bengali | Bengali | complete | complete | written by the team, needs a second reader |
| Santali | Ol Chiki | 12 labels, draft (marked "draft" in the language menu and by a banner) | not written; recipients get Hindi | **pending native-speaker review** |

## Why Santali is a draft

Santali is widely spoken around Jharia and Dhanbad, and Ol Chiki is its constitutionally recognised script. Nobody on
the team is a native speaker yet. A wrong word in a danger warning is worse than a correct Hindi one, so:

- The dashboard shows the Santali draft with a banner ("draft translation, awaiting review") and falls back to Hindi
  for any string not yet translated.
- The alerts service sends Santali recipients the Hindi text until `services/alerts/src/templates/messages.json` has
  a reviewed `sat` block (`_status` not `draft-unreviewed`). `ALERTS_ALLOW_DRAFT_LANGS=1` overrides this for testing.

## How to review

1. A native Santali reader goes through `services/dashboard/src/i18n/sat.json` and adds the remaining keys from
   `en.json`, preferring everyday words a villager would use over technical ones.
2. They write the `sat` block in `services/alerts/src/templates/messages.json` with the same keys as `hi`.
3. Read every critical message aloud to a second speaker. Then set `_status` to `reviewed` in both files and note
   the reviewer's name below (with their permission).

Reviewers: _none yet_.

Current Santali draft (loanwords, unreviewed): Normal ᱵᱷᱟᱜᱮ, Watch ᱱᱤᱜᱽᱨᱟᱱᱤ, Warning ᱦᱩᱥᱤᱭᱟᱨ, Danger ᱵᱤᱯᱚᱫ.
