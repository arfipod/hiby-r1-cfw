# `r1-filectl` protocol v1

R1 Manager connects to the existing Dropbear server and executes
`/usr/bin/r1-filectl`. SCP carries file bytes; all SD mutations are committed by
this helper. Path and name arguments are unpadded URL-safe Base64. The literal
`-` encodes the SD root.

Default private paths are:

```text
.r1-manager/incoming   interrupted upload staging
.r1-manager/trash      reversible same-filesystem Trash
```

Normal browsing hides and rejects this tree.

Successful scalar commands return one JSON object. `list` and `trash-list`
return JSON Lines: a header, entries, and a final `done` record. Failures return
one semantic object and a nonzero exit status, for example:

```json
{"ok":false,"code":"DESTINATION_EXISTS","message":"An item already exists at the destination.","errno":17}
```

Stable commands:

```text
protocol
health
list PATH_B64
stat PATH_B64
mkdir PARENT_B64 NAME_B64
rename PATH_B64 NAME_B64
move SOURCE_B64 DESTINATION_DIRECTORY_B64 fail|skip|replace|keep
delete PATH_B64 empty|recursive
trash PATH_B64
trash-list
restore TRASH_ID
purge TRASH_ID
empty-trash
upload-prepare DESTINATION_B64 SIZE fail|skip|replace|keep
upload-status UPLOAD_ID
upload-commit UPLOAD_ID
upload-cancel UPLOAD_ID
```

A staged upload is prepared first, transferred by SCP to the returned absolute
`payload.part` path, checked for its exact expected byte count, `fsync`ed, and
then renamed to its final destination. An interrupted transfer never appears in
the music library.
