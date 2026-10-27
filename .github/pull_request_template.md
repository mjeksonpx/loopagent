## What changed

<!-- One paragraph. What is true now that was not true before? -->

## Evidence block

Every command you ran, its verbatim output tail, and its exit code. This is the part reviewers read; a
narration of the change is not a substitute.

```
$ <command>
<verbatim output tail>
exit=0

$ <command>
<verbatim output tail>
exit=0
```

## Gate dimensions touched

<!-- Which dimension(s) does this change affect, and which falsification case in scripts/regressions.py
     proves the dimension can still fail? A dimension without a regression case is a slogan. -->

- dimensions:
- new/changed regression case:
- `python3 scripts/regressions.py` result:

## Limits and known gaps

<!-- What this change does NOT prove. Leave empty only if you are sure. -->

## Checklist

- [ ] `make check` is green locally
- [ ] no marker is printed by anything other than the gate (`..._PASS_...` belongs to the gate)
- [ ] no secrets, tokens, absolute personal paths or private repository names in the diff or evidence
- [ ] docs updated where the user-visible verdict changed
