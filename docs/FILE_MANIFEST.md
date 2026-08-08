# Documentation File Manifest

> **Baseline:** post-v1.6.0 documentation maintenance
> **Status:** CI-enforced

Total tracked documentation files: **64**.

Transient `.DS_Store` files and generated build output are excluded. Byte sizes
are deliberately not recorded because they turn ordinary edits into unrelated
manifest churn; the durable contract is file presence and package ownership.

| Package | Directory | Files | Authority |
|---|---|---:|---|
| Master index | `docs/` | 1 | navigation and precedence |
| Product architecture | `01_Product_Architecture/` | 9 | PDS-00 through PDS-08 |
| System architecture | `02_System_Architecture/` | 10 | boundaries, state, data flow and ownership |
| UI system | `03_UI_System/` | 10 | visual, interaction and accessibility rules |
| Data model | `04_Data_Model/` | 7 | feed, option, Greeks, capital and decision contracts |
| Engineering | `05_Engineering/` | 9 | coding, testing, operations and release rules |
| Diagrams | `06_Diagrams/` | 9 | implementation-aligned Mermaid views |
| Audits | `07_Audits/` | 2 | historical and current PDS-01 audits |
| Preserved project docs | `Existing_Project_Docs/` | 2 | original snapshot references |
| Root documentation | `docs/` | 5 | README, changelogs, manifest and current release notes |

## Root documentation files

- `00_MASTER_INDEX.md`
- `README.md`
- `CHANGELOG.md`
- `ARCHITECTURE_CHANGELOG.md`
- `FILE_MANIFEST.md`
- `RELEASE_NOTES_v1.6.0.md`

The master index is counted separately from the five root-support files in the
table. Package file names are enforced by their dedicated CI contract suites;
this manifest gate verifies the recursive total, package counts and required
root documents.
