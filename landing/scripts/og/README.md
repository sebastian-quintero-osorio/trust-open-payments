`og.html` is the source of `public/og.jpg` (1200×630). To regenerate, copy `src/assets/fonts/qontra-trust.woff2` and `barlow-latin-400.woff2` and
`src/assets/brand/trust-wordmark-light.png` next to it, then:

```sh
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --hide-scrollbars \
  --allow-file-access-from-files --force-device-scale-factor=1 --window-size=1200,630 \
  --virtual-time-budget=2000 --screenshot=og.png "file://$PWD/og.html"
sips -s format jpeg -s formatOptions 88 og.png --out ../../public/og.jpg
```
