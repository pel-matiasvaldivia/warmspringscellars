# Site photography

Drop the photographs here with these exact filenames. The page references
them directly and degrades gracefully (placeholder art) if a
file is missing.

| Filename                 | Photo                                                        | Used in                                    |
| ------------------------ | ------------------------------------------------------------ | ------------------------------------------ |
| `cellar-exterior.jpeg`   | The winery building with the arched wooden doors              | Hero — faded/blended behind the headline    |
| `robert-rex.jpeg`        | Robert Rex in the vineyard, straw hat, holding a bottle       | Our Winemakers — left card                  |
| `cecilia-valdivia.jpeg`  | Cecilia Valdivia leaning on a barrel, glass in hand           | Our Winemakers — right card                 |
| `cabernet-bottle.png`    | 2019 Cabernet bottle, cut out onto transparency               | The Wines — first row                       |
| `pinot-bottle.png`       | 2021 Pinot Noir bottle, cut out onto transparency             | The Wines — second row                      |

Recommended: JPEG, sRGB, ~2000px on the long edge, under 400 KB each.
The founder photos are cropped to a 4:5 portrait, so keep faces near the upper
third. The hero image is cropped wide and full-bleed.

## Bottle shots

`cabernet-bottle.png` and `pinot-bottle.png` are generated, not hand-made. The
supplied renders — `WarmSpringsCellars_Cabernet.png` and
`WarmSpringsCellars_Pinot.png` — sit on a grey studio sweep with a cast shadow
and a floor reflection, none of which suits the page's warm background. To
regenerate the cutouts after replacing a render:

```bash
python3 tools/cutout_bottles.py
```

Keep the source renders in the repo so the cutouts stay reproducible. They are
excluded from the container image, which ships only the cutouts.

The group photo of the team has no slot yet — the band that displayed it was
removed because the file never landed here and it 404'd on every page load.
