# FD imputation vs Sleeper native — 2026-07-09

compared: 508 (player, kind) pairs with native FD >= 10

median divergence = 48.9%; over-15% pairs = 491 (96.7%)

Method: see ffi/scoring/fd_impute.py docstring (pooled position rates 2019-2025 + empirical-Bayes player rates, k=50/30/100).

## Pairs over the 15% investigation threshold

| player | pos | kind | native | imputed | div% |
|---|---|---|---|---|---|
| TreVeyon Henderson | RB | rec | 26 | 9.1 | 65% |
| LeQuint Allen | RB | rec | 13 | 4.4 | 65% |
| Rhamondre Stevenson | RB | rec | 21 | 7.8 | 64% |
| Omarion Hampton | RB | rec | 36 | 13.3 | 64% |
| J.K. Dobbins | RB | rec | 19 | 7.0 | 63% |
| Ashton Jeanty | RB | rec | 38 | 14.2 | 63% |
| Dylan Sampson | RB | rec | 18 | 6.6 | 62% |
| Tyquan Thornton | WR | rec | 31 | 11.6 | 62% |
| Chris Brooks | RB | rec | 11 | 4.2 | 62% |
| Derrick Henry | RB | rec | 12 | 4.7 | 61% |
| James Cook | RB | rec | 27 | 10.5 | 61% |
| Jonathan Taylor | RB | rec | 28 | 10.9 | 61% |
| Rashid Shaheed | WR | rec | 63 | 24.8 | 61% |
| Javonte Williams | RB | rec | 24 | 9.4 | 61% |
| Breece Hall | RB | rec | 32 | 12.9 | 60% |
| Saquon Barkley | RB | rec | 26 | 10.7 | 60% |
| Calvin Austin | WR | rec | 20 | 8.2 | 59% |
| Marvin Mims | WR | rec | 29 | 12.0 | 59% |
| Jayden Reed | WR | rec | 73 | 30.0 | 59% |
| D'Andre Swift | RB | rec | 28 | 11.4 | 59% |
| DeMario Douglas | WR | rec | 25 | 10.4 | 59% |
| Tyjae Spears | RB | rec | 25 | 10.3 | 59% |
| Jameson Williams | WR | rec | 100 | 41.6 | 58% |
| Malik Nabers | WR | rec | 98 | 40.8 | 58% |
| Josh Jacobs | RB | rec | 26 | 10.8 | 58% |
| Drew Sample | TE | rec | 13 | 5.3 | 58% |
| Emeka Egbuka | WR | rec | 101 | 42.1 | 58% |
| Jordan Mason | RB | rec | 14 | 5.9 | 58% |
| Kyle Williams | WR | rec | 15 | 6.3 | 58% |
| Troy Franklin | WR | rec | 36 | 15.2 | 58% |
| Xavier Hutchinson | WR | rec | 18 | 7.7 | 58% |
| Quentin Johnston | WR | rec | 75 | 31.9 | 57% |
| Christian Watson | WR | rec | 94 | 40.1 | 57% |
| Rachaad White | RB | rec | 24 | 10.3 | 57% |
| Zay Flowers | WR | rec | 110 | 47.3 | 57% |
| Quinshon Judkins | RB | rec | 23 | 9.8 | 57% |
| Tory Horton | WR | rec | 42 | 18.2 | 57% |
| Malik Washington | WR | rec | 40 | 17.1 | 57% |
| Isaiah Williams | WR | rec | 14 | 5.8 | 57% |
| Kenny Gainwell | RB | rec | 36 | 15.4 | 57% |
| Greg Dortch | WR | rec | 10 | 4.4 | 56% |
| Darnell Mooney | WR | rec | 27 | 12.0 | 56% |
| Tommy DeVito | QB | pass | 20 | 8.9 | 56% |
| Cade Stover | TE | rec | 13 | 5.7 | 56% |
| Jordan Addison | WR | rec | 78 | 34.4 | 56% |
| Jaxon Smith-Njigba | WR | rec | 134 | 59.1 | 56% |
| Brian Thomas | WR | rec | 90 | 39.4 | 56% |
| Travis Hunter | WR | rec | 42 | 18.3 | 56% |
| Wan'Dale Robinson | WR | rec | 68 | 30.2 | 56% |
| Ashton Jeanty | RB | rush | 115 | 51.2 | 56% |
| Tre Tucker | WR | rec | 51 | 22.6 | 55% |
| Marquez Valdes-Scantling | WR | rec | 12 | 5.5 | 55% |
| Nico Collins | WR | rec | 120 | 53.6 | 55% |
| DeVonta Smith | WR | rec | 104 | 46.2 | 55% |
| Dameon Pierce | RB | rush | 11 | 4.7 | 55% |
| Barion Brown | WR | rec | 14 | 6.1 | 55% |
| Chris Brazzell | WR | rec | 38 | 17.0 | 55% |
| Brenen Thompson | WR | rec | 19 | 8.5 | 55% |
| Bhayshul Tuten | RB | rec | 23 | 10.5 | 55% |
| Woody Marks | RB | rec | 14 | 6.1 | 55% |
