const { generate } = require("youtube-po-token-generator");
const fs = require("fs");

generate().then(({ visitorData, poToken }) => {
  fs.writeFileSync("/home/win-htut/discordbot/potoken.json", JSON.stringify({ visitorData, poToken, ts: Date.now() }));
  console.log("✅ po_token refreshed at", new Date().toISOString());
}).catch(e => {
  console.error("❌ Failed:", e.message);
  process.exit(1);
});
