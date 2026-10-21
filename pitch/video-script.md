# Demo video: script and shot list

Target **4:15** (rules: 3–5 min strict, MP4/MOV, ≥ 720p, ≤ 80 MB). Record on 26 Oct on the frozen
build. Export 1280×720, H.264, ~2 Mbps video + 128 kbps audio ≈ 70 MB for 4:15; check the size before
upload. File name: `Jharkhand_ACHAL_DemoVideo.mp4`.

Voice: one narrator per section (names in brackets), spoken in English with Hindi subtitles, or in
Hindi with English subtitles. Keep every claim to what is on screen or in `docs/kpis.md`.

| # | Time | Shot (what the viewer sees) | Narration | Who / prep |
| --- | --- | --- | --- | --- |
| 1 | 0:00–0:20 | Title card: ACHAL अचल logo, "Team ACHAL, NIT Jamshedpur, Jharkhand". Then the three team members on camera, each says name + role in one line | "We are Team ACHAL from NIT Jamshedpur. ACHAL means 'unmoving'. That is what the ground under Jharia's homes should be." | All three; tripod, quiet room |
| 2 | 0:20–0:55 | Map of Jharia coalfield (OpenStreetMap, credited); photos or newspaper headlines of the 2017 and July 2025 Indira Chowk collapses, credited, or our own drawings if rights are unclear | "Under Jharia lie old coal workings and fires that have burned for a century. The 2008 Master Plan listed 595 affected sites. In 2017 a mechanic and his son were swallowed by the ground at Indira Chowk. In July 2025 the same spot caved in again. Resettlement takes years. Today, people learn the ground is failing when their walls crack." | Impact; use only images we have rights to, credit on screen |
| 3 | 0:55–1:25 | Animated diagram (from the deck): posts on the ground, radio hops to gateway, siren, phone | "ACHAL puts a grid of solar-powered sensor posts over a risky site. Each post measures tilt, vibration, crack opening and ground strain, and talks to its neighbours over long-range LoRa radio. The mesh carries readings to a gateway at the village edge, which sounds a siren even with no internet, and an AI turns the readings into a risk map and alerts." | Lead |
| 4 | 1:25–1:45 | Hardware: BOM table on screen, (if available) one breadboarded node; firmware repo tree | "Every part is sourced in India. One post costs ₹3,773, about 40 times cheaper than an imported station. The node, siren and gateway firmware is open source." | Lead; say "prototype" or "reference design", not "deployed" |
| 5 | 1:45–2:35 | **Live demo 1**: terminal `docker compose up`, dashboard at :8080 in English. Normal scenario: green map. Switch scenario to `pillar_collapse`; nodes turn yellow → red, heatmap spreads, node chart shows tilt accelerating; open "Why this alert" panel with reasons and time-to-failure | "Here is the full system running on simulated Jharia data. Normal ground stays green. Now a pillar starts to fail under the centre of the panel. Tilt speeds up, neighbours agree, and the risk engine raises a warning, then critical, with the reasons and an estimated time to failure." | AI drives the laptop; screen-record at 1080p, export 720p |
| 6 | 2:35–3:05 | **Live demo 2 (offline siren)**: start with `SCENARIO=pillar_collapse SIM_SPEED=1200 SIM_SEED=42`; `docker compose stop mosquitto`; split screen: dashboard status strip (gateway offline, buffered count rising) + `docker compose logs -f gateway` showing `SIREN siren-01 ON level=warning ... (gateway, uplink DOWN, local only)` about 70 s after start; then `docker compose start mosquitto`, buffered count drains to 0 in ~15 s and the siren state appears on the dashboard. Alternative proof: `scripts/demo_uplink_outage.sh 120` prints `ZERO READINGS LOST` | "Now we cut the internet. The gateway keeps every reading and still sounds the siren on its own. When the link returns, every reading arrives. Nothing is lost." | Lead |
| 7 | 3:05–3:30 | **Live demo 3 (language)**: switch dashboard to हिन्दी, then বাংলা; phone receives the Hindi SMS (real test send, contact details blurred) | "Alerts reach people in their language. This is the Hindi SMS a resident receives. Bengali works too; Santali is drafted and waiting for a native speaker to check it." | Impact; real phone, number blurred |
| 8 | 3:30–3:55 | KPI slide: measured numbers from `docs/kpis.md` only (lead time, recall, false alarms, delivery with 30% nodes down, cost per node / hectare) | "In [N] simulated runs ACHAL warned [lead time] before collapse, caught [recall] of events, with [false alarm] false alarms per node per month, and delivered 100% of readings with 30% of nodes down." | Fill brackets from W3's report on 26 Oct; drop any line without a number |
| 9 | 3:55–4:15 | Roadmap + team on camera, closing card with GitHub link and MIT licence | "Next we build three real nodes with the prototype grant, test them on campus, and seek a pilot with BCCL. ACHAL is open source, so any coalfield can use it. Thank you." | All three |

## Recording checklist (26 Oct)

- [ ] Frozen build tag checked out; `docker compose up --build` works from a clean clone
- [ ] Demo seeds fixed so the collapse happens on cue (`SIM_SEED=42`, `SIM_SPEED=1200`: first local siren ~70 s after start, per W2's rehearsal 21 Oct); dry-run twice
- [ ] Dashboard zoom 125%, dark mode off, browser bookmarks hidden, notifications off
- [ ] Test SMS sent to a team member's phone; number blurred in the edit
- [ ] Only our own or properly licensed images/music (YouTube Audio Library or none); credits card at the end
- [ ] Duration 3:00–5:00, 720p or higher, ≤ 80 MB, named `Jharkhand_ACHAL_DemoVideo.mp4`
- [ ] Every number on screen matches `docs/kpis.md`
