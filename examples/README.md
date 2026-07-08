# Examples

Things you can ask your MCP client once `vmd-mcp` is connected. All run headlessly.

The repo includes a tiny structure at [`examples/sample.pdb`](sample.pdb), useful
for a first smoke test:

```bash
uv run python scripts/smoke_vmd.py
```

## Inspect a structure

> Load `bpti.pdb` and give me its `molecule_info` — atoms, chains, residues, waters.

> Load `examples/sample.pdb`, count `all`, count `name CA`, then measure the
> distance between `resid 1` and `resid 2`.

## Count a selection

> In `bpti.pdb`, how many atoms match `protein and name CA`? What about
> `water within 4 of protein`?

## Trajectory analysis

> For `md.tpr` + `md.xtc`, compute the CA-`rmsd` aligned to frame 0 and report
> min / max / mean. Then compute `radius_of_gyration` of the protein over the
> trajectory.

> For `md.tpr` + `md.xtc`, compute `rmsf` for `protein and name CA` and identify
> the most flexible residues.

## Contacts and distances

> In `examples/sample.pdb`, find contacts between `resid 1` and `resid 2` with a
> 5 Å cutoff and return at most 10 atom pairs.

> For my trajectory, measure the center-to-center distance between `resname LIG`
> and `protein` for all frames.

## Render a figure (no display needed)

> Render `bpti.pdb` as a `NewCartoon` colored by `Structure` at 1200×900 on a
> white background and save it as `bpti.png`.

> Render `examples/sample.pdb` using the `atomistic_lines` preset and save it as
> `sample.png`.

Useful `render_image` knobs:

| Parameter | Examples |
|-----------|----------|
| `representation` | `NewCartoon`, `Licorice`, `VDW`, `Lines`, `Surf`, `QuickSurf` |
| `coloring` | `Structure`, `Chain`, `Name`, `ResID`, `ResName`, `Beta` |
| `selection` | `protein`, `chain A`, `resid 1 to 58`, `not water` |
| `width` / `height` | any pixel size (VMD rounds width to a multiple) |

Useful `render_preset` values:

| Preset | Best for |
|--------|----------|
| `publication_cartoon` | protein cartoon overview |
| `ligand_detail` | atomistic ligand or pocket render |
| `surface_overview` | solid surface view |
| `quick_surface` | faster all-atom surface preview |
| `atomistic_lines` | small structures and debugging selections |

## Anything else — the escape hatch

`run_tcl` runs arbitrary VMD Tcl. Emit results with `puts "@@VMDMCP@@ key=value"`:

> Run this Tcl: load `bpti.pdb`, select `protein`, and print the number of
> hydrogen bonds with `measure hbonds`, tagged as `@@VMDMCP@@`.
