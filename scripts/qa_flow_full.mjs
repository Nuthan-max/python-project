export default async function run(page, ui) {
  const result = { logs: [] };

  page.on("console", msg => console.log("[BROWSER CONSOLE]", msg.type(), msg.text()));

  await page.waitForTimeout(2000);

  // 1. Click Connaught Place preset
  console.log("1. Setting emergency location...");
  await page.locator("#emergency-presets button").first().click();
  await page.waitForTimeout(1000);

  // 2. Select Hospital
  console.log("2. Selecting destination hospital...");
  const hospSelect = page.locator("#hospital-select");
  await page.waitForFunction(() => document.querySelector("#hospital-select").options.length > 1, { timeout: 6000 });
  await hospSelect.selectOption({ index: 1 });
  await page.waitForTimeout(500);

  // 3. Deploy Ambulance
  console.log("3. Deploying ambulance...");
  await page.locator("#btn-add-ambulance").click();
  await page.waitForTimeout(1000);

  // 4. Click Plan & Dispatch
  console.log("4. Dispatching mission...");
  await page.locator("#btn-plan").click();

  // Wait 4 seconds for mission loop
  await page.waitForTimeout(4000);

  await page.screenshot({ path: "data/qa-routed-mission.png" });
  console.log("Screenshot saved: data/qa-routed-mission.png");

  // 5. Open Analytics Modal
  console.log("5. Testing Analytics modal...");
  await page.locator("#tab-btn-analytics").click();
  await page.waitForTimeout(1500);
  await page.screenshot({ path: "data/qa-analytics-tab.png" });
  console.log("Screenshot saved: data/qa-analytics-tab.png");

  // 6. Close Analytics & Open History
  console.log("6. Testing History modal...");
  await page.locator(".modal-close").first().click();
  await page.waitForTimeout(500);
  await page.locator("#tab-btn-history").click();
  await page.waitForTimeout(1500);
  await page.screenshot({ path: "data/qa-history-tab.png" });
  console.log("Screenshot saved: data/qa-history-tab.png");

  return { success: true };
}
