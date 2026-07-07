# Examples

Things you can ask your MCP client once `vmd-mcp` is connected. All run headlessly.

## Inspect a structure

> Load `bpti.pdb` and give me its `molecule_info` — atoms, chains, residues, waters.

## Count a selection

> In `bpti.pdb`, how many atoms match `protein and name CA`? What about
> `water within 4 of protein`?

## Trajectory analysis

> For `md.tpr` + `md.xtc`, compute the CA-`rmsd` aligned to frame 0 and report
> min / max / mean. Then compute `radius_of_gyration` of the protein over the
> trajectory.

## Render a figure (no display needed)

> Render `bpti.pdb` as a `NewCartoon` colored by `Structure` at 1200×900 on a
> white background and save it as `bpti.png`.

Useful `render_image` knobs:

| Parameter | Examples |
|-----------|----------|
| `representation` | `NewCartoon`, `Licorice`, `VDW`, `Lines`, `Surf`, `QuickSurf` |
| `coloring` | `Structure`, `Chain`, `Name`, `ResID`, `ResName`, `Beta` |
| `selection` | `protein`, `chain A`, `resid 1 to 58`, `not water` |
| `width` / `height` | any pixel size (VMD rounds width to a multiple) |

## Anything else — the escape hatch

`run_tcl` runs arbitrary VMD Tcl. Emit results with `puts "@@VMDMCP@@ key=value"`:

> Run this Tcl: load `bpti.pdb`, select `protein`, and print the number of
> hydrogen bonds with `measure hbonds`, tagged as `@@VMDMCP@@`.
