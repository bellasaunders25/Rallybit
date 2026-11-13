import { startRuntime } from "./startup.js";

const runtime = await startRuntime({
  onRestart: async () => {
    await runtime.stop();
    process.exit(1);
  },
});
for (const signal of ["SIGTERM", "SIGINT"])
  process.once(signal, () =>
    runtime.stop().then(
      () => process.exit(0),
      () => process.exit(1),
    ),
  );
process.on("unhandledRejection", (error) => {
  console.error(
    JSON.stringify({
      event: "unhandled_rejection",
      name: error?.name,
      code: error?.code,
    }),
  );
});
