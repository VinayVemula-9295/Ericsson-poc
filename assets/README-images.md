# Dashboard image asset

IIPP displays `assets/images.png` in the main dashboard header when the file exists.

The supplied v11 project archive did not contain this image binary. Preserve or copy the image already present on the EC2 host to this exact path before running `./deploy.sh`:

```bash
~/cloudpilot-ericsson-poc/assets/images.png
```

The image is optional at runtime; the dashboard remains usable if it is absent.
