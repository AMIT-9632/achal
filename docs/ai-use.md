# AI-use disclosure

The HSC-27 guidelines require disclosure of AI assistance. Team ACHAL used an AI assistant (Claude,
by Anthropic) during the project:

| Where | What the assistant did | What the team did |
| --- | --- | --- |
| Research | Summarised public sources on Jharia, survey methods and earlier hackathon repos | Chose the problem, checked sources, decided what to cite |
| Code | Drafted scaffolding, services, firmware and tests | Reviewed, ran and tested the code; owns the design decisions |
| Hardware | Collected supplier prices with links, drafted BOM, cost and power scripts | Chose components; prices to be re-checked before purchase |
| Docs and pitch | Drafted documentation, deck text, video script and Q&A answers | Edited, fact-checked and will present them |

All final content is the team's responsibility. The AI risk engine inside ACHAL is classical ML
(scikit-learn) trained only on our own synthetic data; no generative AI runs in the product.
The deck's diagrams and the cover illustration were drawn as SVG/HTML code with the assistant's help and reviewed by the team; no image-generation model was used, and no AI-generated voices are used in the video. Icons are from Lucide (ISC licence).
