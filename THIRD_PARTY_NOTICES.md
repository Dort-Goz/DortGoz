# Third-party notices — release gate

This repository is Apache-2.0. Runtime dependency versions are locked in
`backend/uv.lock` and `frontend/bun.lock`. Notices are generated from those
exact installed trees, not from version ranges.

Regenerate with:

```bash
python scripts/sbom.py --out sbom.json --check
```

`--check` is the release license gate. It fails on AGPL, GPL-2/3, LGPL, SSPL,
CC-BY-NC, Commons Clause, BUSL and Elastic-2, and it reports any component whose
license cannot be determined.

## Measured state (2026-08-25)

194 components across both trees. No copyleft-strong license is present, and no
component has an undeclared license.

| License | Components |
|---|---:|
| MIT (incl. `MIT License`) | 144 |
| BSD-3-Clause (incl. `Modified BSD License`, `3-Clause BSD License`) | 15 |
| Apache-2.0 (incl. spelling variants) | 12 |
| ISC | 7 |
| MPL-2.0 (alone or combined) | 4 |
| BSD-2-Clause | 2 |
| PSF-2.0 | 1 |
| Other permissive (0BSD, Zlib, CC0-1.0, CC-BY-4.0, AFL-2.1/BSD-3-Clause) | 9 |

MPL-2.0 components are `certifi` and the `lightningcss` build-time family. MPL is
file-level copyleft: obligations attach only to modified MPL files. None are
modified, and `lightningcss` does not ship in the browser bundle.

## Included project artifacts

| Component | License/provenance | Release state |
|---|---|---|
| Dörtgöz source code | Apache-2.0, repository `LICENSE` | Included |
| `motion-baseline-v1` JSON configuration | Team-authored, MIT declaration in `models/MANIFEST.json` | Included with SHA-256 |
| VLM weights | Not included | Must have an Apache-2.0/MIT local manifest and SHA-256 |
| FFmpeg/ffprobe executable | Not included | The developer machine's GPL-enabled build is expressly excluded; the operator installs FFmpeg from their distribution |

## Decisions

1. **License policy — RESOLVED 2026-08-25 (team captain).** The written rule is
   now "open-source (OSI) licenses, as the şartname allows; AGPL and SSPL are
   banned". The release gate keeps its stricter ban list (GPL/LGPL/SSPL/AGPL and
   source-available licenses) because this repository ships as Apache-2.0;
   adding a strong-copyleft component needs an explicit team decision. The
   measured tree already satisfies the policy with zero gate hits.
2. **FFmpeg distribution — OPEN.** The decision to require a system FFmpeg
   rather than ship a binary is recorded above; the approved build for the final
   machine is still to be named.
3. **UCA annotations — RESOLVED 2026-08-25 (team captain).** The three UCA JSON
   files left the public repository. `scripts/fetch_uca.py` downloads them from
   the upstream repository and verifies SHA-256 (all three hashes were confirmed
   byte-identical against upstream on 2026-08-25). Attribution lives in the
   public `data/uca/CITATION.bib`. The Apache-2.0 vs academic-only contradiction
   no longer affects our release; it only affects local development copies.

Until item 2 is closed, the offline bundle must not include an FFmpeg binary;
with a system FFmpeg it may be labelled a redistributable final release once the
remaining submission gates in the audit report close.
