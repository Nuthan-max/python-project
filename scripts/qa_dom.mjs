// DOM probe for the RescueRoute command centre.
// node <skill>/browser.mjs http://127.0.0.1:5000/ --script scripts/qa_dom.mjs
export default async function run(page) {
  await page.waitForTimeout(9000);
  return await page.evaluate(() => ({
    mapCanvas: document.querySelectorAll("#map canvas").length,
    mapSvgPaths: document.querySelectorAll("#map svg path").length,
    engine: document.querySelector("#chip-engine")?.innerText,
    network: document.querySelector("#chip-network")?.innerText,
    hospitalOptions: document.querySelectorAll("#hospital-select option").length,
    presetButtons: document.querySelectorAll("#emergency-presets button").length,
    toasts: [...document.querySelectorAll(".toast")].map((t) => t.innerText),
    bodyChars: document.body.innerText.length,
  }));
}
