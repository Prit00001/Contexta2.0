import { parseSse } from "./client";

test("parseSse returns complete events and keeps the partial tail", () => {
  const { events, rest } = parseSse('event: token\ndata: "Hel"\n\nevent: token\ndata: "lo"\n\nevent: do');
  expect(events).toEqual([
    { event: "token", data: "Hel" },
    { event: "token", data: "lo" },
  ]);
  expect(rest).toBe("event: do");
  expect(parseSse(rest + 'ne\ndata: {"answer":"x"}\n\n').events).toEqual([{ event: "done", data: { answer: "x" } }]);
});

test("parseSse ignores blocks without an event or data line", () => {
  expect(parseSse(": keep-alive\n\n").events).toEqual([]);
});
