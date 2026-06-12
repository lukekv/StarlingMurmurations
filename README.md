# StarlingMurmurations
The Texture Library Image Sorter is an automated pipeline that turns disorganized texture files into a clean, searchable library. It groups related images, removes duplicates, tests tileability, and uses a local AI model to categorize materials before generating a visual HTML browser.

## Releases

To share the app with someone who doesn't have Python installed, cut a release:

```
git tag v1.0.0
git push --tags
```

The Release workflow builds the GUI with PyInstaller on a Windows runner, packages it with Inno Setup, and attaches `StarlingMurmurations-Setup-<version>.exe` to a GitHub Release. The installer puts the app in Program Files with a Start Menu shortcut; no Python required on the target machine.

For a dry run without creating a Release, trigger the **Release** workflow manually from the Actions tab (`workflow_dispatch`) — the installer is uploaded as a build artifact instead.

**Prerequisite on the target machine:** the AI tagging stage needs [Ollama](https://ollama.com) running locally with a vision-capable model (default `gemma4:e4b`). Everything else — scanning, dedup, tileability, file ops — works without it.
