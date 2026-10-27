# ACHAL compared with survey methods

ACHAL does not replace surveying; it fills the gap between surveys. The table is for the deck's
comparison slide and for jury questions. Accuracy and revisit figures are typical published values
for each method; cost figures for ACHAL come from [`hardware/cost-summary.md`](../hardware/cost-summary.md).

| | Satellite InSAR (Sentinel-1) | Continuous GNSS station | Total station / levelling survey | **ACHAL surface mesh** |
| --- | --- | --- | --- | --- |
| What it measures | Line-of-sight ground displacement over wide areas | 3-D position of one point | Position / height of survey prisms or benchmarks | Tilt, crack opening, ground strain between nodes, vibration, ground temperature, inter-node radio signal |
| Precision | mm–cm per year for slow, coherent motion | mm (horizontal) to cm (vertical) after processing | mm | Reports tilt in 0.001° and crack/strain in 0.01 mm steps; real accuracy (MPU6050 temperature drift, potentiometer linearity) still to be measured on the bench |
| How often | One image every 6–12 days | Continuous, results after processing | When a surveyor visits (weeks–months) | Every 60 s, plus instant wake on vibration bursts |
| Sees a pillar collapse forming in hours? | No: too slow between passes, and fast motion breaks interferometric coherence | Only if a station happens to stand on the spot | No | **Yes**: designed for it (vibration bursts, accelerating tilt, inverse-velocity time-to-failure) |
| Works over active mining, vegetation, monsoon | Coherence loss over active mines and vegetation; monsoon soil swelling adds noise | Yes | Yes, but access is hard | Yes; ground-coupled sensors |
| Warns people directly | No (analysis product) | No | No | **Siren on site with no internet, SMS in Hindi, Bengali, English** (Santali after native review) |
| Points per site | Thousands of radar scatterers, uneven | 1–few (cost) | Tens of benchmarks | 25 per hectare in the demo grid |
| Cost | Data free (Copernicus); expert processing | High per point (survey-grade receiver) | Instrument + surveyor time for every visit | **₹4,240 per node**, ₹1.25 lakh for a 1-ha panel kit with gateway and 2 sirens |
| Role with ACHAL | **Screen** the coalfield and choose where to deploy | **Anchor** a site to absolute coordinates | **Calibrate** nodes at install | **Watch** the chosen sites continuously and warn |

## How they work together

1. InSAR maps (free Sentinel-1 data) flag the fastest-sinking settlements, like the 0.44 km² in
   Eastern Jharia moving more than 7 cm/year ([Thakur et al. 2024](https://isprs-annals.copernicus.org/articles/X-4-2024/349/2024/isprs-annals-X-4-2024-349-2024.pdf)).
2. ACHAL nodes go onto those sites. A total-station survey at install fixes each node's position.
3. ACHAL watches minute by minute, sounds the siren and alerts residents and BCCL/DGMS.
4. Later rounds: compare each node's tilt and strain history with InSAR time series over the same
   ground to validate both.

## Compared with other low-cost systems

| System | Domain | Difference from ACHAL |
| --- | --- | --- |
| Amrita A-LEWS (Munnar, Sikkim) | Landslides, wireless sensor network | Same idea of multilevel warnings over a WSN; ACHAL targets mine subsidence and pillar collapse with a physics-based simulator and per-node cost under ₹4,000 |
| IIT Mandi low-cost landslide EWS | Landslides; MEMS sensors, hooters + SMS | Similar community-alarm model; ACHAL adds a self-healing LoRa mesh, multi-node consensus and time-to-failure |
| Hackathon repos for SIH26025 (see [related-work.md](related-work.md)) | Mine safety | None of the 8 public repos we reviewed combines a surface mesh with node-to-node extensometers, a sourced BOM, an offline siren carried by the mesh beacon, and explainable alerts in Hindi, Bengali and English with Santali under native review |
