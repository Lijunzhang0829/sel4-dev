# Downstream session ROOT edits

These sessions reference merge-group sessions in their ROOT
declarations. After merge:
- `= <merge-X> +` → `= HaskellMega +`
- `sessions <merge-X>` → drop (HaskellMega already in `+` chain)
  OR `sessions HaskellMega` if not in `+` chain.

### `CamkesAdlSpec` (in `/home/lijun/seL4-docker-main/verification/l4v/camkes/ROOT`)
- `parent Access`  →  edit needed

### `CamkesCdlBase` (in `/home/lijun/seL4-docker-main/verification/l4v/camkes/ROOT`)
- `parent DPolicy`  →  edit needed

### `LibTest` (in `/home/lijun/seL4-docker-main/verification/l4v/lib/ROOT`)
- `parent Refine`  →  edit needed

### `CBaseRefine` (in `/home/lijun/seL4-docker-main/verification/l4v/proof/ROOT`)
- `parent Refine`  →  edit needed

### `InfoFlowCBase` (in `/home/lijun/seL4-docker-main/verification/l4v/proof/ROOT`)
- `sessions Access`  →  edit needed
- `sessions InfoFlow`  →  edit needed

### `SysInit` (in `/home/lijun/seL4-docker-main/verification/l4v/sys-init/ROOT`)
- `parent DSpecProofs`  →  edit needed
- `sessions SepDSpec`  →  edit needed
- `sessions DSpecProofs`  →  edit needed
