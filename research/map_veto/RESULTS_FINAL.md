## FINAL - veto-1-a140ca69ec


### FINAL

| format | target | model | matches | log-loss [95% CI] | Brier | top-1 |
|---|---|---|---|---|---|---|
| bo1 | map | M0 uniform-legal | 273 | 1.9459 [1.9459, 1.9459] | 0.8571 | 0.1795 |
| bo1 | map | M1 simple recent | 273 | 1.7280 [1.6385, 1.8082] | 0.8056 | 0.293 |
| bo1 | map | M3 candidate | 273 | 1.6779 [1.6018, 1.7550] | 0.7964 | 0.3443 |
| bo1 | map | M3 without listing order | 273 | 1.6770 [1.6006, 1.7541] | 0.7961 | 0.3443 |
| bo1 | set | M0 uniform-legal | 273 | 1.9459 [1.9459, 1.9459] | 0.8571 | 0.1795 |
| bo1 | set | M1 simple recent | 273 | 1.7280 [1.6385, 1.8082] | 0.8056 | 0.293 |
| bo1 | set | M3 candidate | 273 | 1.6779 [1.6018, 1.7550] | 0.7964 | 0.3443 |
| bo1 | set | M3 without listing order | 273 | 1.6770 [1.6006, 1.7541] | 0.7961 | 0.3443 |
| bo1 | seq | M0 uniform-legal | 273 | 1.9459 [1.9459, 1.9459] | 0.8571 | 0.1795 |
| bo1 | seq | M1 simple recent | 273 | 1.7280 [1.6385, 1.8082] | 0.8056 | 0.293 |
| bo1 | seq | M3 candidate | 273 | 1.6779 [1.6018, 1.7550] | 0.7964 | 0.3443 |
| bo1 | seq | M3 without listing order | 273 | 1.6770 [1.6006, 1.7541] | 0.7961 | 0.3443 |
| bo1 | incl | M0 uniform-legal | 273 | 0.4101 [0.4101, 0.4101] | 0.1224 | 0.1795 |
| bo1 | incl | M1 simple recent | 273 | 0.3751 [0.3599, 0.3890] | 0.1151 | 0.293 |
| bo1 | incl | M3 candidate | 273 | 0.3672 [0.3533, 0.3810] | 0.1138 | 0.3443 |
| bo1 | incl | M3 without listing order | 273 | 0.3670 [0.3532, 0.3809] | 0.1137 | 0.3443 |
| bo3 | map1 | M0 uniform-legal | 2320 | 1.9459 [1.9459, 1.9459] | 0.8571 | 0.1638 |
| bo3 | map1 | M1 simple recent | 2320 | 1.7803 [1.7597, 1.8013] | 0.8157 | 0.2517 |
| bo3 | map1 | M2 frozen sim | 2000 | 1.7642 [1.7422, 1.7842] | 0.8072 | 0.2755 |
| bo3 | map1 | M2 frozen (+uniform fallback) | 2320 | 1.7893 [1.7701, 1.8080] | 0.8141 | 0.2647 |
| bo3 | map1 | M3 candidate | 2320 | 1.6728 [1.6469, 1.6989] | 0.7862 | 0.297 |
| bo3 | map1 | M3 without listing order | 2320 | 1.6739 [1.6478, 1.6992] | 0.7869 | 0.2931 |
| bo3 | map2 | M0 uniform-legal | 2320 | 1.9459 [1.9459, 1.9459] | 0.8571 | 0.1573 |
| bo3 | map2 | M1 simple recent | 2320 | 1.7653 [1.7495, 1.7815] | 0.8065 | 0.2746 |
| bo3 | map2 | M2 frozen sim | 2000 | 1.6856 [1.6704, 1.7004] | 0.8044 | 0.283 |
| bo3 | map2 | M2 frozen (+uniform fallback) | 2320 | 1.7215 [1.7080, 1.7351] | 0.8117 | 0.2711 |
| bo3 | map2 | M3 candidate | 2320 | 1.6505 [1.6286, 1.6736] | 0.7776 | 0.3091 |
| bo3 | map2 | M3 without listing order | 2320 | 1.6533 [1.6323, 1.6751] | 0.7791 | 0.3043 |
| bo3 | decider | M0 uniform-legal | 2320 | 1.9459 [1.9459, 1.9459] | 0.8571 | 0.1922 |
| bo3 | decider | M1 simple recent | 2320 | 1.7349 [1.7183, 1.7511] | 0.8043 | 0.269 |
| bo3 | decider | M3 candidate | 2320 | 1.6095 [1.5888, 1.6316] | 0.7669 | 0.3405 |
| bo3 | decider | M3 without listing order | 2320 | 1.6096 [1.5887, 1.6318] | 0.7669 | 0.3401 |
| bo3 | pair | M0 uniform-legal | 2320 | 3.7377 [3.7377, 3.7377] | 0.9762 | 0.0129 |
| bo3 | pair | M1 simple recent | 2320 | 3.2834 [3.2524, 3.3169] | 0.9465 | 0.1134 |
| bo3 | pair | M2 frozen sim | 2000 | 3.3254 [3.2969, 3.3531] | 0.9502 | 0.109 |
| bo3 | pair | M2 frozen (+uniform fallback) | 2320 | 3.3823 [3.3576, 3.4087] | 0.9538 | 0.0957 |
| bo3 | pair | M3 candidate | 2320 | 2.9818 [2.9416, 3.0232] | 0.9193 | 0.1522 |
| bo3 | pair | M3 without listing order | 2320 | 2.9844 [2.9443, 3.0249] | 0.9197 | 0.1474 |
| bo3 | set | M0 uniform-legal | 2320 | 3.5553 [3.5553, 3.5553] | 0.9714 | 0.0138 |
| bo3 | set | M1 simple recent | 2320 | 2.7525 [2.7230, 2.7861] | 0.8994 | 0.2254 |
| bo3 | set | M3 candidate | 2320 | 2.4337 [2.3971, 2.4714] | 0.8581 | 0.2728 |
| bo3 | set | M3 without listing order | 2320 | 2.4326 [2.3959, 2.4701] | 0.8579 | 0.2737 |
| bo3 | seq | M0 uniform-legal | 2320 | 5.3471 [5.3471, 5.3471] | 0.9952 | 0.0013 |
| bo3 | seq | M1 simple recent | 2320 | 4.4950 [4.4583, 4.5350] | 0.9804 | 0.0642 |
| bo3 | seq | M3 candidate | 2320 | 4.0762 [4.0278, 4.1276] | 0.9656 | 0.0901 |
| bo3 | seq | M3 without listing order | 2320 | 4.0786 [4.0295, 4.1301] | 0.9659 | 0.0866 |
| bo3 | incl | M0 uniform-legal | 2320 | 0.6829 [0.6829, 0.6829] | 0.2449 | 0.3816 |
| bo3 | incl | M1 simple recent | 2320 | 0.5519 [0.5469, 0.5577] | 0.1857 | 0.6733 |
| bo3 | incl | M3 candidate | 2320 | 0.5022 [0.4960, 0.5086] | 0.1678 | 0.6986 |
| bo3 | incl | M3 without listing order | 2320 | 0.5020 [0.4958, 0.5084] | 0.1677 | 0.6996 |
| bo5 | map1 | M0 uniform-legal | 23 | 1.9459 [1.9459, 1.9459] | 0.8571 | 0.2174 |
| bo5 | map1 | M1 simple recent | 23 | 1.7843 [1.5699, 2.0016] | 0.8092 | 0.3913 |
| bo5 | map1 | M3 candidate | 23 | 1.7965 [1.5324, 2.0698] | 0.8192 | 0.2609 |
| bo5 | map1 | M3 without listing order | 23 | 1.7678 [1.5048, 2.0330] | 0.8085 | 0.3043 |
| bo5 | map2 | M0 uniform-legal | 23 | 1.9459 [1.9459, 1.9459] | 0.8571 | 0.1739 |
| bo5 | map2 | M1 simple recent | 23 | 1.7528 [1.6210, 1.9024] | 0.809 | 0.2174 |
| bo5 | map2 | M3 candidate | 23 | 1.5024 [1.3877, 1.6448] | 0.7477 | 0.3478 |
| bo5 | map2 | M3 without listing order | 23 | 1.4695 [1.3627, 1.5976] | 0.7341 | 0.3913 |
| bo5 | map3 | M0 uniform-legal | 23 | 1.9459 [1.9459, 1.9459] | 0.8571 | 0.2174 |
| bo5 | map3 | M1 simple recent | 23 | 1.8248 [1.7538, 1.9049] | 0.8263 | 0.1739 |
| bo5 | map3 | M3 candidate | 23 | 1.7290 [1.5434, 1.9624] | 0.8033 | 0.3478 |
| bo5 | map3 | M3 without listing order | 23 | 1.7103 [1.5232, 1.9395] | 0.796 | 0.3478 |
| bo5 | map4 | M0 uniform-legal | 23 | 1.9459 [1.9459, 1.9459] | 0.8571 | 0.087 |
| bo5 | map4 | M1 simple recent | 23 | 1.9294 [1.8368, 2.0308] | 0.8528 | 0.1304 |
| bo5 | map4 | M3 candidate | 23 | 1.6656 [1.5634, 1.7697] | 0.8047 | 0.1739 |
| bo5 | map4 | M3 without listing order | 23 | 1.6447 [1.5425, 1.7536] | 0.7965 | 0.2174 |
| bo5 | decider | M0 uniform-legal | 23 | 1.9459 [1.9459, 1.9459] | 0.8571 | 0.0435 |
| bo5 | decider | M1 simple recent | 23 | 1.7043 [1.5089, 1.9033] | 0.782 | 0.3478 |
| bo5 | decider | M3 candidate | 23 | 1.5474 [1.1185, 2.0422] | 0.6951 | 0.4348 |
| bo5 | decider | M3 without listing order | 23 | 1.5448 [1.1158, 2.0393] | 0.6943 | 0.4348 |
| bo5 | set | M0 uniform-legal | 23 | 3.0445 [3.0445, 3.0445] | 0.9524 | 0.0 |
| bo5 | set | M1 simple recent | 23 | 2.0093 [1.8234, 2.2152] | 0.7906 | 0.6087 |
| bo5 | set | M3 candidate | 23 | 1.1879 [0.6141, 1.9572] | 0.4047 | 0.7391 |
| bo5 | set | M3 without listing order | 23 | 1.1887 [0.6145, 1.9574] | 0.4051 | 0.7391 |
| bo5 | seq | M0 uniform-legal | 23 | 7.8320 [7.8320, 7.8320] | 0.9996 | 0.0 |
| bo5 | seq | M1 simple recent | 23 | 6.3134 [5.8178, 6.8056] | 0.9949 | 0.087 |
| bo5 | seq | M3 candidate | 23 | 5.2164 [4.3766, 6.2559] | 0.978 | 0.0435 |
| bo5 | seq | M3 without listing order | 23 | 5.1719 [4.3194, 6.2155] | 0.9767 | 0.0435 |
| bo5 | incl | M0 uniform-legal | 23 | 0.5983 [0.5983, 0.5983] | 0.2041 | 0.6609 |
| bo5 | incl | M1 simple recent | 23 | 0.4337 [0.4019, 0.4689] | 0.1368 | 0.9043 |
| bo5 | incl | M3 candidate | 23 | 0.2642 [0.1536, 0.4074] | 0.0783 | 0.9304 |
| bo5 | incl | M3 without listing order | 23 | 0.2643 [0.1538, 0.4074] | 0.0783 | 0.9304 |

Head-to-head on M2's coverage (BO3 matches where the frozen simulator returns a forecast):

| target | model | matches | log-loss [95% CI] | top-1 |
|---|---|---|---|---|
| map1 | M0 uniform-legal | 2000 | 1.9459 [1.9459, 1.9459] | 0.1585 |
| map1 | M1 simple recent | 2000 | 1.7561 [1.7332, 1.7787] | 0.2625 |
| map1 | M2 frozen sim | 2000 | 1.7642 [1.7422, 1.7842] | 0.2755 |
| map1 | M3 candidate | 2000 | 1.6383 [1.6115, 1.6651] | 0.313 |
| map1 | M3 without listing order | 2000 | 1.6408 [1.6140, 1.6676] | 0.309 |
| map2 | M0 uniform-legal | 2000 | 1.9459 [1.9459, 1.9459] | 0.151 |
| map2 | M1 simple recent | 2000 | 1.7597 [1.7424, 1.7776] | 0.272 |
| map2 | M2 frozen sim | 2000 | 1.6856 [1.6704, 1.7004] | 0.283 |
| map2 | M3 candidate | 2000 | 1.6396 [1.6161, 1.6631] | 0.313 |
| map2 | M3 without listing order | 2000 | 1.6435 [1.6205, 1.6662] | 0.3035 |
| pair | M0 uniform-legal | 2000 | 3.7377 [3.7377, 3.7377] | 0.013 |
| pair | M1 simple recent | 2000 | 3.2452 [3.2102, 3.2796] | 0.1215 |
| pair | M2 frozen sim | 2000 | 3.3254 [3.2969, 3.3531] | 0.109 |
| pair | M3 candidate | 2000 | 2.9217 [2.8766, 2.9661] | 0.1685 |
| pair | M3 without listing order | 2000 | 2.9260 [2.8822, 2.9700] | 0.161 |

Paired log-loss difference (baseline - candidate; > 0 = candidate better), match-grouped 95% CI:

| format | target | comparison | matches | mean diff [95% CI] |
|---|---|---|---|---|
| bo1 | incl | M0-M3 | 273 | 0.0429 [0.0291, 0.0564] |
| bo1 | incl | M1-M3 | 273 | 0.0079 [-0.0032, 0.0184] |
| bo1 | incl | M3nl-M3 | 273 | -0.0002 [-0.0005, 0.0001] |
| bo1 | map | M0-M3 | 273 | 0.2680 [0.1924, 0.3445] |
| bo1 | map | M1-M3 | 273 | 0.0501 [-0.0122, 0.1093] |
| bo1 | map | M3nl-M3 | 273 | -0.0009 [-0.0026, 0.0009] |
| bo1 | seq | M0-M3 | 273 | 0.2680 [0.1924, 0.3445] |
| bo1 | seq | M1-M3 | 273 | 0.0501 [-0.0122, 0.1093] |
| bo1 | seq | M3nl-M3 | 273 | -0.0009 [-0.0026, 0.0009] |
| bo1 | set | M0-M3 | 273 | 0.2680 [0.1924, 0.3445] |
| bo1 | set | M1-M3 | 273 | 0.0501 [-0.0122, 0.1093] |
| bo1 | set | M3nl-M3 | 273 | -0.0009 [-0.0026, 0.0009] |
| bo3 | decider | M0-M3 | 2320 | 0.3364 [0.3154, 0.3578] |
| bo3 | decider | M1-M3 | 2320 | 0.1253 [0.1094, 0.1417] |
| bo3 | decider | M3nl-M3 | 2320 | 0.0000 [-0.0005, 0.0006] |
| bo3 | incl | M0-M3 | 2320 | 0.1807 [0.1744, 0.1870] |
| bo3 | incl | M1-M3 | 2320 | 0.0498 [0.0465, 0.0531] |
| bo3 | incl | M3nl-M3 | 2320 | -0.0002 [-0.0003, -0.0001] |
| bo3 | map1 | M0-M3 | 2320 | 0.2731 [0.2484, 0.2976] |
| bo3 | map1 | M1-M3 | 2320 | 0.1074 [0.0930, 0.1219] |
| bo3 | map1 | M2-M3 | 2000 | 0.1259 [0.1087, 0.1436] |
| bo3 | map1 | M2full-M3 | 2320 | 0.1164 [0.0999, 0.1330] |
| bo3 | map1 | M3nl-M3 | 2320 | 0.0011 [-0.0030, 0.0054] |
| bo3 | map2 | M0-M3 | 2320 | 0.2954 [0.2732, 0.3163] |
| bo3 | map2 | M1-M3 | 2320 | 0.1147 [0.1010, 0.1281] |
| bo3 | map2 | M2-M3 | 2000 | 0.0460 [0.0293, 0.0626] |
| bo3 | map2 | M2full-M3 | 2320 | 0.0709 [0.0549, 0.0866] |
| bo3 | map2 | M3nl-M3 | 2320 | 0.0028 [-0.0016, 0.0076] |
| bo3 | pair | M0-M3 | 2320 | 0.7559 [0.7160, 0.7974] |
| bo3 | pair | M1-M3 | 2320 | 0.3016 [0.2782, 0.3246] |
| bo3 | pair | M2-M3 | 2000 | 0.4038 [0.3726, 0.4342] |
| bo3 | pair | M2full-M3 | 2320 | 0.4005 [0.3731, 0.4303] |
| bo3 | pair | M3nl-M3 | 2320 | 0.0025 [-0.0026, 0.0079] |
| bo3 | seq | M0-M3 | 2320 | 1.2709 [1.2231, 1.3193] |
| bo3 | seq | M1-M3 | 2320 | 0.4187 [0.3902, 0.4473] |
| bo3 | seq | M3nl-M3 | 2320 | 0.0023 [-0.0027, 0.0078] |
| bo3 | set | M0-M3 | 2320 | 1.1217 [1.0842, 1.1578] |
| bo3 | set | M1-M3 | 2320 | 0.3188 [0.2988, 0.3385] |
| bo3 | set | M3nl-M3 | 2320 | -0.0010 [-0.0020, -0.0001] |
| bo5 | decider | M0-M3 | 23 | 0.3985 [-0.0572, 0.8203] |
| bo5 | decider | M1-M3 | 23 | 0.1569 [-0.2001, 0.4589] |
| bo5 | decider | M3nl-M3 | 23 | -0.0025 [-0.0087, 0.0016] |
| bo5 | incl | M0-M3 | 23 | 0.3341 [0.1992, 0.4482] |
| bo5 | incl | M1-M3 | 23 | 0.1695 [0.0576, 0.2635] |
| bo5 | incl | M3nl-M3 | 23 | 0.0001 [-0.0001, 0.0004] |
| bo5 | map1 | M0-M3 | 23 | 0.1494 [-0.1164, 0.4188] |
| bo5 | map1 | M1-M3 | 23 | -0.0122 [-0.1675, 0.1474] |
| bo5 | map1 | M3nl-M3 | 23 | -0.0287 [-0.0637, 0.0005] |
| bo5 | map2 | M0-M3 | 23 | 0.4435 [0.3230, 0.5684] |
| bo5 | map2 | M1-M3 | 23 | 0.2504 [0.1359, 0.3725] |
| bo5 | map2 | M3nl-M3 | 23 | -0.0329 [-0.0661, -0.0067] |
| bo5 | map3 | M0-M3 | 23 | 0.2169 [-0.0125, 0.4105] |
| bo5 | map3 | M1-M3 | 23 | 0.0958 [-0.0697, 0.2451] |
| bo5 | map3 | M3nl-M3 | 23 | -0.0186 [-0.0452, -0.0007] |
| bo5 | map4 | M0-M3 | 23 | 0.2803 [0.1761, 0.3874] |
| bo5 | map4 | M1-M3 | 23 | 0.2638 [0.1521, 0.3682] |
| bo5 | map4 | M3nl-M3 | 23 | -0.0209 [-0.0523, -0.0013] |
| bo5 | seq | M0-M3 | 23 | 2.6156 [1.6679, 3.4689] |
| bo5 | seq | M1-M3 | 23 | 1.0970 [0.4028, 1.6702] |
| bo5 | seq | M3nl-M3 | 23 | -0.0445 [-0.0890, -0.0084] |
| bo5 | set | M0-M3 | 23 | 1.8567 [1.1271, 2.4386] |
| bo5 | set | M1-M3 | 23 | 0.8214 [0.2355, 1.2689] |
| bo5 | set | M3nl-M3 | 23 | 0.0008 [-0.0001, 0.0021] |

### Live updates (conditioned on the first 2 / 4 confirmed actions; M2 cannot condition)

| period | format | known | target | model | matches | log-loss [95% CI] | top-1 |
|---|---|---|---|---|---|---|---|
| FINAL | bo1 | after2 | map | M0 uniform-legal | 273 | 1.6094 [1.6094, 1.6094] | 0.2234 |
| FINAL | bo1 | after2 | map | M1 simple recent | 273 | 1.4831 [1.4072, 1.5566] | 0.359 |
| FINAL | bo1 | after2 | map | M3 candidate | 273 | 1.4737 [1.4079, 1.5398] | 0.3333 |
| FINAL | bo1 | after2 | seq | M0 uniform-legal | 273 | 1.6094 [1.6094, 1.6094] | 0.2234 |
| FINAL | bo1 | after2 | seq | M1 simple recent | 273 | 1.4831 [1.4072, 1.5566] | 0.359 |
| FINAL | bo1 | after2 | seq | M3 candidate | 273 | 1.4737 [1.4079, 1.5398] | 0.3333 |
| FINAL | bo1 | after2 | set | M0 uniform-legal | 273 | 1.6094 [1.6094, 1.6094] | 0.2234 |
| FINAL | bo1 | after2 | set | M1 simple recent | 273 | 1.4831 [1.4072, 1.5566] | 0.359 |
| FINAL | bo1 | after2 | set | M3 candidate | 273 | 1.4737 [1.4079, 1.5398] | 0.3333 |
| FINAL | bo1 | after4 | map | M0 uniform-legal | 273 | 1.0986 [1.0986, 1.0986] | 0.3223 |
| FINAL | bo1 | after4 | map | M1 simple recent | 273 | 1.0461 [0.9897, 1.1023] | 0.4725 |
| FINAL | bo1 | after4 | map | M3 candidate | 273 | 1.0552 [1.0020, 1.1077] | 0.4469 |
| FINAL | bo1 | after4 | seq | M0 uniform-legal | 273 | 1.0986 [1.0986, 1.0986] | 0.3223 |
| FINAL | bo1 | after4 | seq | M1 simple recent | 273 | 1.0461 [0.9897, 1.1023] | 0.4725 |
| FINAL | bo1 | after4 | seq | M3 candidate | 273 | 1.0552 [1.0020, 1.1077] | 0.4469 |
| FINAL | bo1 | after4 | set | M0 uniform-legal | 273 | 1.0986 [1.0986, 1.0986] | 0.3223 |
| FINAL | bo1 | after4 | set | M1 simple recent | 273 | 1.0461 [0.9897, 1.1023] | 0.4725 |
| FINAL | bo1 | after4 | set | M3 candidate | 273 | 1.0552 [1.0020, 1.1077] | 0.4469 |
| FINAL | bo3 | after2 | decider | M0 uniform-legal | 2320 | 1.6094 [1.6094, 1.6094] | 0.228 |
| FINAL | bo3 | after2 | decider | M1 simple recent | 2320 | 1.5501 [1.5351, 1.5648] | 0.2862 |
| FINAL | bo3 | after2 | decider | M3 candidate | 2320 | 1.4914 [1.4709, 1.5114] | 0.353 |
| FINAL | bo3 | after2 | map1 | M0 uniform-legal | 2320 | 1.6094 [1.6094, 1.6094] | 0.2099 |
| FINAL | bo3 | after2 | map1 | M1 simple recent | 2320 | 1.3102 [1.2853, 1.3346] | 0.4681 |
| FINAL | bo3 | after2 | map1 | M3 candidate | 2320 | 1.1589 [1.1265, 1.1919] | 0.5453 |
| FINAL | bo3 | after2 | map2 | M0 uniform-legal | 2320 | 1.6094 [1.6094, 1.6094] | 0.1983 |
| FINAL | bo3 | after2 | map2 | M1 simple recent | 2320 | 1.2667 [1.2452, 1.2871] | 0.5116 |
| FINAL | bo3 | after2 | map2 | M3 candidate | 2320 | 1.0948 [1.0649, 1.1237] | 0.5815 |
| FINAL | bo3 | after2 | seq | M0 uniform-legal | 2320 | 4.0943 [4.0943, 4.0943] | 0.0142 |
| FINAL | bo3 | after2 | seq | M1 simple recent | 2320 | 3.3732 [3.3347, 3.4121] | 0.144 |
| FINAL | bo3 | after2 | seq | M3 candidate | 2320 | 3.0962 [3.0440, 3.1450] | 0.2065 |
| FINAL | bo3 | after2 | set | M0 uniform-legal | 2320 | 2.3026 [2.3026, 2.3026] | 0.0849 |
| FINAL | bo3 | after2 | set | M1 simple recent | 2320 | 1.9587 [1.9332, 1.9872] | 0.3078 |
| FINAL | bo3 | after2 | set | M3 candidate | 2320 | 1.9033 [1.8740, 1.9351] | 0.3207 |
| FINAL | bo3 | after4 | decider | M0 uniform-legal | 2320 | 1.0986 [1.0986, 1.0986] | 0.3375 |
| FINAL | bo3 | after4 | decider | M1 simple recent | 2320 | 0.9331 [0.9147, 0.9507] | 0.5453 |
| FINAL | bo3 | after4 | decider | M3 candidate | 2320 | 0.9291 [0.9110, 0.9472] | 0.5608 |
| FINAL | bo3 | after4 | map1 | M0 uniform-legal | 2320 | 0.0000 [0.0000, 0.0000] | 1.0 |
| FINAL | bo3 | after4 | map1 | M1 simple recent | 2320 | 0.0000 [0.0000, 0.0000] | 1.0 |
| FINAL | bo3 | after4 | map1 | M3 candidate | 2320 | 0.0000 [0.0000, 0.0000] | 1.0 |
| FINAL | bo3 | after4 | map2 | M0 uniform-legal | 2320 | 0.0000 [0.0000, 0.0000] | 1.0 |
| FINAL | bo3 | after4 | map2 | M1 simple recent | 2320 | 0.0000 [0.0000, 0.0000] | 1.0 |
| FINAL | bo3 | after4 | map2 | M3 candidate | 2320 | 0.0000 [0.0000, 0.0000] | 1.0 |
| FINAL | bo3 | after4 | seq | M0 uniform-legal | 2320 | 1.0986 [1.0986, 1.0986] | 0.3375 |
| FINAL | bo3 | after4 | seq | M1 simple recent | 2320 | 0.9331 [0.9147, 0.9507] | 0.5453 |
| FINAL | bo3 | after4 | seq | M3 candidate | 2320 | 0.9291 [0.9110, 0.9472] | 0.5608 |
| FINAL | bo3 | after4 | set | M0 uniform-legal | 2320 | 1.0986 [1.0986, 1.0986] | 0.3375 |
| FINAL | bo3 | after4 | set | M1 simple recent | 2320 | 0.9331 [0.9147, 0.9507] | 0.5453 |
| FINAL | bo3 | after4 | set | M3 candidate | 2320 | 0.9291 [0.9110, 0.9472] | 0.5608 |
| FINAL | bo1 | after2 | map | diff M0-M3 | 273 | 0.1357 [0.0696, 0.2015] | |
| FINAL | bo1 | after2 | map | diff M1-M3 | 273 | 0.0093 [-0.0472, 0.0629] | |
| FINAL | bo1 | after2 | seq | diff M0-M3 | 273 | 0.1357 [0.0696, 0.2015] | |
| FINAL | bo1 | after2 | seq | diff M1-M3 | 273 | 0.0093 [-0.0472, 0.0629] | |
| FINAL | bo1 | after2 | set | diff M0-M3 | 273 | 0.1357 [0.0696, 0.2015] | |
| FINAL | bo1 | after2 | set | diff M1-M3 | 273 | 0.0093 [-0.0472, 0.0629] | |
| FINAL | bo1 | after4 | map | diff M0-M3 | 273 | 0.0434 [-0.0091, 0.0966] | |
| FINAL | bo1 | after4 | map | diff M1-M3 | 273 | -0.0091 [-0.0493, 0.0312] | |
| FINAL | bo1 | after4 | seq | diff M0-M3 | 273 | 0.0434 [-0.0091, 0.0966] | |
| FINAL | bo1 | after4 | seq | diff M1-M3 | 273 | -0.0091 [-0.0493, 0.0312] | |
| FINAL | bo1 | after4 | set | diff M0-M3 | 273 | 0.0434 [-0.0091, 0.0966] | |
| FINAL | bo1 | after4 | set | diff M1-M3 | 273 | -0.0091 [-0.0493, 0.0312] | |
| FINAL | bo3 | after2 | decider | diff M0-M3 | 2320 | 0.1181 [0.0980, 0.1385] | |
| FINAL | bo3 | after2 | decider | diff M1-M3 | 2320 | 0.0587 [0.0428, 0.0739] | |
| FINAL | bo3 | after2 | map1 | diff M0-M3 | 2320 | 0.4505 [0.4175, 0.4830] | |
| FINAL | bo3 | after2 | map1 | diff M1-M3 | 2320 | 0.1512 [0.1315, 0.1702] | |
| FINAL | bo3 | after2 | map2 | diff M0-M3 | 2320 | 0.5146 [0.4857, 0.5445] | |
| FINAL | bo3 | after2 | map2 | diff M1-M3 | 2320 | 0.1719 [0.1536, 0.1916] | |
| FINAL | bo3 | after2 | seq | diff M0-M3 | 2320 | 0.9981 [0.9494, 1.0503] | |
| FINAL | bo3 | after2 | seq | diff M1-M3 | 2320 | 0.2770 [0.2467, 0.3091] | |
| FINAL | bo3 | after2 | set | diff M0-M3 | 2320 | 0.3993 [0.3675, 0.4285] | |
| FINAL | bo3 | after2 | set | diff M1-M3 | 2320 | 0.0554 [0.0381, 0.0742] | |
| FINAL | bo3 | after4 | decider | diff M0-M3 | 2320 | 0.1695 [0.1514, 0.1876] | |
| FINAL | bo3 | after4 | decider | diff M1-M3 | 2320 | 0.0040 [-0.0092, 0.0178] | |
| FINAL | bo3 | after4 | map1 | diff M0-M3 | 2320 | -0.0000 [-0.0000, -0.0000] | |
| FINAL | bo3 | after4 | map1 | diff M1-M3 | 2320 | -0.0000 [-0.0000, 0.0000] | |
| FINAL | bo3 | after4 | map2 | diff M0-M3 | 2320 | -0.0000 [-0.0000, -0.0000] | |
| FINAL | bo3 | after4 | map2 | diff M1-M3 | 2320 | -0.0000 [-0.0000, 0.0000] | |
| FINAL | bo3 | after4 | seq | diff M0-M3 | 2320 | 0.1695 [0.1514, 0.1876] | |
| FINAL | bo3 | after4 | seq | diff M1-M3 | 2320 | 0.0040 [-0.0092, 0.0178] | |
| FINAL | bo3 | after4 | set | diff M0-M3 | 2320 | 0.1695 [0.1514, 0.1876] | |
| FINAL | bo3 | after4 | set | diff M1-M3 | 2320 | 0.0040 [-0.0092, 0.0178] | |

### Calibration (ECE, 10 bins, all pool maps per match)

| group | format | model | target | ECE |
|---|---|---|---|---|
| FINAL | bo1 | M0 uniform-legal | incl | 0.0 |
| FINAL | bo1 | M0 uniform-legal | pos1 | 0.0 |
| FINAL | bo1 | M1 simple recent | incl | 0.0228 |
| FINAL | bo1 | M1 simple recent | pos1 | 0.0228 |
| FINAL | bo1 | M3 without listing order | incl | 0.0157 |
| FINAL | bo1 | M3 without listing order | pos1 | 0.0157 |
| FINAL | bo1 | M3 candidate | incl | 0.017 |
| FINAL | bo1 | M3 candidate | pos1 | 0.017 |
| FINAL | bo3 | M0 uniform-legal | incl | 0.0 |
| FINAL | bo3 | M0 uniform-legal | pos1 | 0.0 |
| FINAL | bo3 | M0 uniform-legal | pos2 | 0.0 |
| FINAL | bo3 | M1 simple recent | incl | 0.0595 |
| FINAL | bo3 | M1 simple recent | pos1 | 0.0195 |
| FINAL | bo3 | M1 simple recent | pos2 | 0.0163 |
| FINAL | bo3 | M2 frozen (+uniform fallback) | pos1 | 0.0268 |
| FINAL | bo3 | M2 frozen (+uniform fallback) | pos2 | 0.0198 |
| FINAL | bo3 | M2 frozen sim | pos1 | 0.0311 |
| FINAL | bo3 | M2 frozen sim | pos2 | 0.023 |
| FINAL | bo3 | M3 without listing order | incl | 0.0438 |
| FINAL | bo3 | M3 without listing order | pos1 | 0.0088 |
| FINAL | bo3 | M3 without listing order | pos2 | 0.0111 |
| FINAL | bo3 | M3 candidate | incl | 0.0433 |
| FINAL | bo3 | M3 candidate | pos1 | 0.0095 |
| FINAL | bo3 | M3 candidate | pos2 | 0.0085 |
| FINAL | bo5 | M0 uniform-legal | incl | 0.0 |
| FINAL | bo5 | M0 uniform-legal | pos1 | 0.0 |
| FINAL | bo5 | M0 uniform-legal | pos2 | 0.0 |
| FINAL | bo5 | M1 simple recent | incl | 0.163 |
| FINAL | bo5 | M1 simple recent | pos1 | 0.0269 |
| FINAL | bo5 | M1 simple recent | pos2 | 0.0139 |
| FINAL | bo5 | M3 without listing order | incl | 0.0367 |
| FINAL | bo5 | M3 without listing order | pos1 | 0.0659 |
| FINAL | bo5 | M3 without listing order | pos2 | 0.0601 |
| FINAL | bo5 | M3 candidate | incl | 0.0367 |
| FINAL | bo5 | M3 candidate | pos1 | 0.0537 |
| FINAL | bo5 | M3 candidate | pos2 | 0.0498 |

Coverage: {"FINAL|bo3|all": 2320, "FINAL|bo3|M2": 2000, "FINAL|bo5|all": 23, "FINAL|bo1|all": 273}
