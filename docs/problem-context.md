# Problem context: subsidence in the Jharia coalfield

Sources are linked inline; numbers are quoted as the source gives them. Use this page for the deck
(slides 1–2), the video and jury answers.

## The place

- **Jharia coalfield, Dhanbad district, Jharkhand.** India's main source of prime coking coal,
  mined since the 1890s, largely by Bharat Coking Coal Ltd (BCCL). Old shallow bord-and-pillar
  workings, many abandoned, and underground coal fires lie beneath towns, villages, roads and
  railway lines.
- **Fire and subsidence are linked.** Fires burn away coal pillars that hold up the roof of old
  workings; voids then migrate to the surface as cracks, troughs and sudden potholes.

## Scale of exposure

- The original **Jharia Master Plan (2008)** listed **595 fire- and subsidence-affected sites** with
  **98,314 houses**, of which **79,159** were proposed for resettlement. By March 2016 only **4,049
  families (about 5%)** had been relocated; JRDA surveys counted about **1.2 lakh non-BCCL families**
  in affected areas, and about **91,000 unauthorised houses** against an expected 23,847
  ([The Caravan](https://caravanmagazine.in/vantage/jharia-coalfields-residents-fear-subsidence-little-promise-rehabilitation)).
- On **25 June 2025** the Union Cabinet approved a **Revised Jharia Master Plan** with an outlay of
  **₹5,940.47 crore**, taking a phased approach that starts with the most vulnerable sites, with a
  ₹1 lakh livelihood grant and up to ₹3 lakh credit support per family
  ([DD India](https://ddindia.co.in/2025/06/cabinet-approves-revised-jharia-master-plan-with-%E2%82%B95940-crore-financial-outlay/),
  [Indian PSU](https://indianpsu.com/cabinet-approves-revised-master-plan-for-dealing-with-fire-subsidence-and-rehabilitation-of-affected-families-in-jharia-coalfield/)).
  Resettlement takes years; people keep living on unstable ground in the meantime.

## What happens on the ground

- **2017, Indira Chowk, Jharia:** the ground opened beside a roadside garage and buried the
  mechanic and his young son; their bodies were never recovered
  ([The Caravan](https://caravanmagazine.in/vantage/jharia-coalfields-residents-fear-subsidence-little-promise-rehabilitation),
  [The Week/PTI](https://www.theweek.in/wire-updates/national/2025/07/19/ces3-jh-land-subsidence.html)).
- **August 2017, Mohari Baandh colony:** 10–12 houses sank and 100–150 more were damaged; residents
  saw cracks, heard a thud and ran ([The Caravan](https://caravanmagazine.in/vantage/jharia-coalfields-residents-fear-subsidence-little-promise-rehabilitation)).
- **18 July 2025, again beside Indira Chowk:** a crater about 10 ft wide and 15 ft deep swallowed a
  parked mini truck at 9:45 pm; nobody was hurt. DGMS had declared the area dangerous because of an
  underground fire ([The Week/PTI](https://www.theweek.in/wire-updates/national/2025/07/19/ces3-jh-land-subsidence.html)).
- Satellite radar shows the ground moving across the coalfield: in Eastern Jharia (2018–2021,
  Sentinel-1 PS-InSAR) **0.44 km² sank faster than 7 cm/year** and 2.78 km² at 3–7 cm/year, with the
  largest swings in the monsoon months
  ([Thakur et al., ISPRS Annals 2024](https://isprs-annals.copernicus.org/articles/X-4-2024/349/2024/isprs-annals-X-4-2024-349-2024.pdf)).

## The gap ACHAL fills

1. **No early warning at house level.** The reports above describe people learning of a collapse
   from cracks in their walls and the sound of the ground giving way. Rehabilitation plans move
   people over years; nothing tells a household tonight that the ground under it is moving faster.
2. **Survey methods are slow or sparse.** Satellite InSAR maps slow trough subsidence over weeks
   but cannot see a pothole forming in hours and loses coherence over active mining; GNSS and total
   stations are precise but expensive per point or manual (see [comparison.md](comparison.md)).
3. **Warnings must work offline and in local languages.** Many affected settlements have weak
   connectivity; residents speak Hindi, Khortha, Bengali and Santali. A warning that needs the
   internet or English does not reach them.
4. **Cost.** Imported geotechnical stations cost ₹1.5–4.5 lakh per point (as cited by
   RONALD-REX-7/sih26025), so dense coverage over hundreds of sites is unaffordable.

## Beneficiaries

| Who | What ACHAL gives them |
| --- | --- |
| Residents of fire/subsidence-affected settlements (incl. non-title holders) | Siren at the village edge and SMS in their language before the ground fails |
| BCCL / colliery safety officers, DGMS | Continuous, explainable risk map per panel; evidence for evacuation and prioritising resettlement |
| JRDA and district administration (Dhanbad DDMA) | Which sites to resettle first, based on measured movement rather than complaints |
| Road and rail operators crossing the coalfield | Alerts on assets above old workings |
| Mine workers on the surface | Same alarms near haul roads and colliery infrastructure |

Other Jharkhand coalfields with the same pattern: Raniganj (shared with West Bengal), Bokaro,
Ramgarh, Karanpura. Each is a new `config/sites/*.yaml` file, not new code.
