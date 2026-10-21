# References (re-implemented, not copied)

- Knothe, S. (1957). Influence-function theory of surface subsidence over mine workings.
- Fukuzono, T. (1985). Inverse-velocity method for predicting time of slope failure.
- Liu, F. T., Ting, K. M., Zhou, Z.-H. (2008). Isolation Forest. ICDM.
- Peng, S. S. (1992). Surface Subsidence Engineering. SME.

## Field simulator (W1)

Formulas are re-implemented from the textbook forms; see `docs/field-sim-model.md` for where each is used.

- Knothe, S. (1957). Observations of surface movements under influence of mining and their
  theoretical interpretation. *Proc. European Congress on Ground Movement*, Leeds, 210-218.
  Influence function, erf trough profile, time function dS/dt = c (S_final - S).
- Aversin, S. G. (1947) and Budryk, W. (1953): horizontal displacement proportional to slope,
  U = B dS/dx. Originals not consulted; used as presented in Kratzsch (1983) and Peng (1992).
- Kratzsch, H. (1983). *Mining Subsidence Engineering*. Springer, Berlin.
  ISBN 978-3-642-81925-4.
- Peng, S. S. (1992). *Surface Subsidence Engineering*. SME, Littleton, CO. Subsidence factor,
  angle of draw, radius of influence, damage classification.
- National Coal Board (1975). *Subsidence Engineers' Handbook*. NCB Mining Department, London.
  Strain and tilt damage categories (basis of the trough damage thresholds).
- Fukuzono, T. (1985). A new method for predicting the failure time of a slope. *Proc. 4th
  International Conference and Field Workshop on Landslides*, Tokyo, 145-150. Linear inverse
  velocity before failure (precursor creep).
- Utsu, T. (1961). A statistical study on the occurrence of aftershocks. *Geophysical Magazine*
  30, 521-605. Omori-Utsu decay used for post-collapse aftershocks.
- Sheorey, P. R., Loui, J. P., Singh, K. B., Singh, S. K. (2000). Ground subsidence observations
  and a modified influence function method for complete subsidence prediction. *International
  Journal of Rock Mechanics and Mining Sciences* 37(5), 801-818. Indian coalfield observations;
  the calibration target for the subsidence factor and r (not yet applied).

W3 adds full citations and DOIs for the risk-engine formulas. DOIs for the W1 items above are to
be verified before the final submission rather than guessed.
