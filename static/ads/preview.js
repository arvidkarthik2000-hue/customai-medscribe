import { requireSession, renderHeader } from "/js/common.js";
import { mountSlot, unmountAllSlots, openResourcesTray } from "/ads/slot.js";

const $ = (id) => document.getElementById(id);

async function start() {
  const me = await requireSession();
  renderHeader("doctor", { subtitle: "Ad slot preview" });
  const prescriberId = me.prescriber.id;
  const opts = () => ({ prescriberId, specialty: $("spec").value, state: $("state").value });
  $("mount").addEventListener("click", async () => {
    unmountAllSlots();
    const hs = await Promise.all([
      mountSlot($("s1"), { slot: "schedule_card", ...opts() }),
      mountSlot($("s2"), { slot: "drafting_wait", ...opts() }),
      mountSlot($("s3"), { slot: "post_sign", ...opts() }),
    ]);
    $("status").textContent = hs.map((h) => `${h.slot}: ${h.empty ? "nothing eligible" : h.brand}`).join(" · ");
  });
  $("unmount").addEventListener("click", () => { unmountAllSlots(); $("status").textContent = "All slots unmounted."; });
  $("tray").addEventListener("click", () => openResourcesTray(opts()));
}

start();
