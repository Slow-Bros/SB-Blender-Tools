# Releasing add-ons

Add-ons are distributed as a Blender extension repository: a folder containing
the packaged zips and an `index.json` listing them. Blender reads that JSON over
plain HTTP, so no server is needed — GitHub serves the files directly.

The repository lives in `extensions/` and users point Blender at:

```
https://raw.githubusercontent.com/Slow-Bros/SBTools/main/extensions/index.json
```

Two consequences follow from that URL:

- **The repository must stay public.** `raw.githubusercontent.com` returns
  nothing for private repositories, and Blender has no way to authenticate
  against GitHub.
- **Only `main` is published.** A release becomes visible once the commit
  carrying it has been merged into `main`, not while it sits on `develop` or a
  feature branch.

## Making a release

1. Raise `version` in `blender/<add-on>/blender_manifest.toml`. Blender detects
   an update purely by comparing this number, so an unchanged version ships to
   nobody, however different the zip is.

2. Build the zip:

   ```powershell
   .\scripts\build_addon.ps1 ai_retopo
   ```

3. Copy it from `dist/` into `extensions/`. `dist/` is the throwaway output
   directory and stays ignored by git; `extensions/` holds only builds that are
   deliberately published.

   ```powershell
   Copy-Item dist\ai_retopo-0.1.0.zip extensions\
   ```

4. Regenerate the index. The command scans every zip in the folder and rewrites
   `index.json`:

   ```powershell
   & "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" --command extension server-generate --repo-dir=extensions
   ```

5. Commit the version bump, the zip and the regenerated `index.json` together,
   then merge to `main` and tag the release.

## Rules for `extensions/`

- **Never edit `index.json` by hand.** It records the byte size and SHA256 of
  every zip, and Blender verifies both after downloading. A hand-edited entry
  fails the check instead of installing.
- **Every listed zip must actually be present.** Deleting an old zip without
  regenerating the index leaves an entry pointing at a missing file.
- Old zips may be kept or removed as you like — `index.json` only ever lists the
  newest version per add-on id. Keeping them costs a few kilobytes and lets a
  user reinstall an earlier build by hand.
- Adding a second add-on needs no extra work: put its zip in the same folder and
  regenerate, and it appears alongside the existing ones.

## Adding the repository in Blender

Preferences → Get Extensions → the dropdown at the top right → Repositories →
`+` → *Add Remote Repository*, then paste the URL above. Leave the access token
empty and enable *Check for Updates on Startup*.

`raw.githubusercontent.com` is served through a CDN with a cache of a few
minutes. A release pushed a moment ago may still show the previous version in
Blender; that is the cache, not a broken index.
