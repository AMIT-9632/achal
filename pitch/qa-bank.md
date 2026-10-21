# Jury Q&A bank

Answers are written to be said in about 30 seconds. Numbers in `[KPI: ...]` brackets are filled from
`docs/kpis.md` once W3's harness has run; never quote a number that is not in that file.
"Owner" is the team role who answers first (see `CONTRIBUTORS.md`): **Lead** = team lead / systems,
**AI** = AI and GIS engineer, **Impact** = product, impact and communication.

## Problem and relevance

1. **Why Jharia, why subsidence?** (Impact) Jharia has 595 fire- and subsidence-affected sites
   under towns and roads, listed in the 2008 Master Plan, and a ₹5,940 crore revised plan approved in
   June 2025. In 2017 a mechanic and his son were swallowed by the ground at Indira Chowk; in July
   2025 the same spot caved in again. Resettlement takes years; ACHAL protects people while they
   still live there.
2. **Isn't resettlement the real solution?** (Impact) Yes, and ACHAL does not replace it. It covers
   the years in between, and it gives JRDA and BCCL measured movement to decide which sites to move
   first, instead of complaints after damage.
3. **Who exactly benefits?** (Impact) Households on affected sites, many without legal title;
   colliery safety officers and DGMS; JRDA and the district disaster authority; road and rail
   operators. One 1-hectare kit covers about 900 people in our demo site.
4. **How do you know residents get no warning today?** (Impact) Reporting on the 2017 Mohari
   Baandh collapse describes residents noticing cracks, hearing a thud and running. We found no
   public description of a house-level warning system in Jharia. Our next step is to ask residents
   and BCCL directly.
5. **Which VBYLD theme and SDGs?** (Impact) Theme 7, AI, GIS & Emerging Tech, overlapping theme 11,
   Disaster Mitigation. SDG 11.5 disaster losses, 3.9 hazard deaths, 8.8 safe work, 9.1 resilient
   infrastructure, 13.1 climate resilience (`docs/sdg-mapping.md`).

## Solution and technology

6. **In one line, what is ACHAL?** (Lead) A grid of ₹3,800 solar-powered sensor posts that talk to
   each other over long-range radio, an AI that turns their readings into a risk map, and a siren
   that sounds in the village even when the internet is down.
7. **What does each node measure?** (Lead) Ground tilt in two axes and vibration from an
   accelerometer, opening of a crack gauge, stretch of a wire to the next node, ground temperature
   for fire areas, battery, and the radio signal it hears from each neighbour.
8. **Why a mesh and not just a SIM card in each node?** (Lead) Mobile coverage is patchy over the
   coalfield and a SIM per node costs every month. LoRa reaches hundreds of metres per hop with no
   subscription; the mesh relays around dead nodes; only the gateway needs an uplink, and even that
   is optional for the siren.
9. **How does the mesh heal itself?** (Lead) The gateway floods a beacon every minute; each node
   picks the neighbour with the fewest hops to the gateway as its parent. If the parent does not
   forward its frame, it retries through the next-best neighbour, and parents that fail twice are
   dropped until they work again. In simulation, with 30% of nodes dead, the mesh delivered 100% of readings versus 98.85% for single-hop radio `[docs/kpis.md:
   delivery]`.
10. **What is the radio signal between neighbours for?** (AI) If two posts move apart or one
    tilts, the signal between them changes. It costs nothing extra and the risk engine can use it as
    a second, independent hint of movement.
11. **Why LoRa, which band, is it legal?** (Lead) LoRa trades bit rate for range and very low
    power, which suits a 50-byte reading per minute. We use 865–867 MHz, India's licence-exempt
    band, at 17 dBm, well below the 1 W limit.
12. **What happens when the internet goes down?** (Lead) The gateway keeps every reading in a local
    buffer and replays it later: 4,225 of 4,225 readings arrived after a 2-hour (field-time) broker outage. It runs the safety rules itself
    and switches sirens through the mesh beacon, so the village is warned with no cloud at all.
13. **What does the AI actually do?** (AI) Four layers: rules on rates of change, an Isolation
    Forest that learns what normal ground looks like, a check that at least two neighbouring nodes
    agree, and an inverse-velocity estimate of time to failure. They fuse into one 0–1 risk score
    per node with reasons, smoothed so the level does not flicker.
14. **Why not deep learning?** (AI) We have no labelled field data yet. Isolation Forest learns
    from normal data only and is explainable; inverse velocity is a proven method from slope
    monitoring. An LSTM forecaster is on the roadmap once real data exists.
15. **What is inverse velocity?** (AI) Fukuzono's method: before failure, ground speeds up, so
    1/velocity falls roughly in a straight line towards zero. Extending the line gives a
    time-to-failure estimate. We show it as hours remaining in the alert.
16. **How do you avoid false alarms?** (AI) Rates, not single readings; at least two neighbours
    must agree; hysteresis on levels; sensor-fault detection for stuck, spiking or drifting sensors.
    Measured: `[KPI: false alarms per node per month]`.
17. **How do you explain an alert to a villager or an officer?** (AI) Every alert carries reason
    codes, like "tilt rising fast" or "neighbours agree", shown in their language on the dashboard
    and in the SMS, with the time-to-failure estimate.
18. **How did you model Jharia without field data?** (AI) A physics simulator: Knothe's influence
    function for the subsidence trough over a 100 × 100 m bord-and-pillar panel at 60 m depth with
    2.5 m extraction, tilt and strain derived from it, plus pillar collapse with accelerating creep,
    sensor noise, drift and faults, and fire-heated ground. Five scenarios, seeded and reproducible.
19. **Isn't a simulated result meaningless?** (AI) It proves the pipeline and the algorithms work
    on physically plausible signals, and every number is reproducible from a seed. It is not field
    validation; that is the first thing we do with the ₹6,000 prototype grant and BCCL access.
20. **Why MPU6050? It drifts.** (Lead) It costs ₹122 and has a motion-wake interrupt. Its
    temperature drift is real, so the AI looks at rates and neighbour agreement rather than absolute
    tilt, and the ADXL355 is the drop-in precision upgrade on the same I2C bus.
21. **How is the crack gauge and extensometer so cheap?** (Lead) A 10 kΩ slide potentiometer read
    ratiometrically by a 16-bit ADC, spring-returned, with a stainless wire to the next node. Accuracy
    has to be checked on the bench against a dial gauge.
22. **What is the gateway?** (Lead) A Raspberry Pi with an ESP32 LoRa radio on USB. The same Docker
    service that runs in our demo runs on the Pi; with hardware attached, a small bridge feeds real
    radio frames into it instead of the simulator.

## Functionality and demo

23. **What is actually working today?** (Lead) The whole software chain end to end in Docker:
    simulator, mesh, gateway with buffer and offline siren, MQTT, risk engine, backend, dashboard
    with map and charts, multilingual alerts. Firmware for nodes, sirens and the gateway radio is
    written and unit-tested on the PC but not yet flashed to boards.
24. **Can we see the offline siren?** (Lead) Yes: in the demo we stop the broker, trigger a pillar
    collapse, the gateway turns the siren on by itself, and when the broker returns every buffered
    reading arrives.
25. **How fast does a warning reach a phone?** (Lead) Readings arrive every 60 s; the risk engine
    scores each one as it arrives, and SMS goes out through the gateway's provider within seconds.
    Measured lead time before collapse: `[KPI: warning lead time]`.
26. **Can it work in Hindi, Bengali, Santali?** (Impact) Dashboard and SMS in Hindi, Bengali and
    English today. Santali in Ol Chiki is drafted but marked for native-speaker review, because a
    wrong word in a danger message is worse than a correct Hindi one; until then Santali speakers
    get Hindi.
27. **How do you add a new coalfield?** (AI) Write one YAML file: panel geometry, depth, node grid,
    sirens, languages, people exposed. No code change.

## Cost, scale and sustainability

28. **What does one node cost?** (Lead) ₹3,773 including GST at retail single-unit prices, each
    line linked to an Indian supplier in `hardware/bom.csv`. Our target is ₹3,000; bulk cells and
    modules get us there.
29. **And a whole site?** (Lead) 25 nodes, a gateway and two sirens for one hectare: ₹1.13 lakh,
    about ₹126 per person protected in our demo site.
30. **Compared with what?** (Lead) Imported monitoring stations are quoted at ₹1.5–4.5 lakh per
    point, so 40–120 times one ACHAL node. Another SIH design estimates ₹4,850 for an underground
    node.
31. **Why not just use satellite InSAR?** (AI) We use it to choose where to deploy. InSAR is free
    and great for slow trends, but it images every 6–12 days and cannot see a pothole forming in
    hours; it also loses coherence over active mining. ACHAL fills that gap.
32. **How long do batteries last?** (Lead) Calculated from datasheets: 26 days without any sun for
    a leaf node and 8 days for a relay at one reading per minute; a 2 W panel gives several times the
    daily need. At 5-minute sampling a relay lasts 38 days. Bench measurement is pending.
33. **Who pays and who maintains it?** (Impact) The natural owner is the mine operator (BCCL) under
    its safety obligations, with JRDA and the district disaster authority as users. Hardware is
    cheap enough for CSR or district disaster funds; local youth can be trained to maintain posts.
34. **How does it scale to the whole coalfield?** (Lead) A site is a config file; each gateway serves
    one or a few panels; brokers bridge to a central server; the database partitions by site. InSAR
    screening ranks the sites, and kits go to the riskiest first.
35. **What about theft and vandalism?** (Impact) Posts are cheap, have no valuable screen or SIM,
    and the community is the beneficiary. A tamper flag reports a knocked-over node. Placement with
    the panchayat and residents is part of deployment.

## Originality, IP and responsibility

36. **Others built mine-safety mesh projects for SIH. What is new?** (Impact) We studied 8 public
    repos and cite them. None combines a surface mesh with node-to-node extensometers, a sourced
    BOM, a siren carried by the mesh beacon that works offline, a physics-based simulator, and
    explainable alerts in local languages.
37. **Is the code original?** (Lead) Written by us, MIT licensed, with every dependency and its
    licence listed in `docs/ip-register.md`. We copied no code from other repos; sensor drivers are
    written from datasheets.
38. **Did you use AI tools?** (Lead) Yes, and we disclose it in `docs/ai-use.md`: an AI assistant
    helped with research summaries, scaffolding and drafts. Design decisions, review and testing are
    ours. The risk engine itself is classical ML trained only on our synthetic data.
39. **Any data privacy issue?** (Impact) Sensors measure ground, not people. Alert recipients'
    phone numbers stay in the alerts service config and are never published; the delivery log
    omits contact details.
40. **What if the system misses a collapse?** (AI) No warning system is perfect; ACHAL adds a
    layer and does not replace DGMS inspections or evacuation orders. We publish recall and false
    alarm rates openly and tune for recall on critical events.
41. **What if it raises a false alarm and people stop trusting it?** (Impact) Four levels: only
    "critical" sounds the siren; "watch" goes to officials only. Neighbour consensus and hysteresis
    keep false alarms low, and every alert says why it fired.

## Team and roadmap

42. **Who does what?** (Impact) Team lead: mesh, gateway, backend, DevOps. AI and GIS engineer:
    simulator, risk engine, KPIs. Product and impact: dashboard, languages, deck and video, field
    outreach.
43. **What will you do with the ₹6,000 grant and mentorship?** (Lead) Build and bench-test three
    nodes and a gateway, measure current draw, tilt drift and radio range on campus, then ask BCCL
    for a pilot on one affected site.
44. **What is the roadmap?** (Lead) Nov: bench prototype and range test. Dec–Jan: 5-node field
    trial with BCCL/DGMS permission, InSAR comparison, Santali review. 2027: 1-hectare pilot, LSTM
    forecaster once real data exists, ADXL355 nodes, custom PCB.
45. **What could stop you?** (Impact) Site access and permissions, power at the gateway, and
    community trust. We plan to work through NIT Jamshedpur, BCCL and the district administration
    and to involve residents in placing the posts.
