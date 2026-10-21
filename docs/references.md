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

## Risk engine (W3)

- Fukuzono, T. (1985), as above: linear inverse velocity before failure, fitted in its
  integrated form d = a - A ln(t_f - t) for the time-to-failure estimate.
- Voight, B. (1988). A method for prediction of volcanic eruptions. *Nature* 332, 125-130.
  doi:10.1038/332125a0. Generalised failure-forecast law (alpha = 2 gives Fukuzono's line).
- Liu, F. T., Ting, K. M., Zhou, Z.-H. (2008). Isolation Forest. *Proc. 8th IEEE International
  Conference on Data Mining (ICDM)*, 413-422. doi:10.1109/ICDM.2008.17. Used via scikit-learn.
- Bjerrum, L. (1963). Allowable settlement of structures. *Proc. European Conference on Soil
  Mechanics and Foundation Engineering*, Wiesbaden, vol. 3, 135-137. Angular distortion limits
  1/500 and 1/300 behind the tilt-level thresholds.
- Hampel, F. R. (1974). The influence curve and its role in robust estimation. *Journal of the
  American Statistical Association* 69(346), 383-393. doi:10.1080/01621459.1974.10482962.
  MAD-based outlier rejection (1.4826 x MAD).
- Shepard, D. (1968). A two-dimensional interpolation function for irregularly-spaced data.
  *Proc. 23rd ACM National Conference*, 517-524. doi:10.1145/800186.810616. IDW risk grid.
