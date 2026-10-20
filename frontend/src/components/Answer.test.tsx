import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AnswerView } from "./Answer";
import { askResponse } from "../test/fixtures";
import { toViews } from "../pages/Search";

test("renders claims, flags uncited sentences and opens the cited passage", async () => {
  render(<AnswerView data={askResponse} sources={toViews(askResponse.sources)} status="gpt-oss-120b · 1.2s" />);
  expect(screen.getByText("It is popular.")).toHaveClass("claim", "uncited");
  expect(screen.getByText("1 of 2 sentences cite a source", { exact: false })).toBeInTheDocument();
  const passage = screen.getByText("RAG combines a retriever with a generator.");
  expect(passage.closest("details")).not.toHaveAttribute("open");
  await userEvent.click(screen.getByRole("button", { name: "Source 1: Retrieval-Augmented Generation" }));
  expect(passage.closest("details")).toHaveAttribute("open");
  expect(screen.getByRole("link", { name: "Retrieval-Augmented Generation" })).toHaveAttribute("rel", "noopener noreferrer");
});

test("untrusted text is rendered as text, never as markup", () => {
  const evil = '<img src=x onerror="alert(1)">';
  render(
    <AnswerView
      data={{ ...askResponse, claims: [{ text: evil, markers: [], status: "uncited" }] }}
      sources={[{ title: evil, meta: "", text: evil }]}
    />,
  );
  expect(screen.getAllByText(evil).length).toBeGreaterThan(0);
  expect(document.querySelector("img")).toBeNull();
});

test("without claims, [n] markers in the plain answer become citation buttons", () => {
  render(<AnswerView data={{ ...askResponse, claims: [] }} sources={toViews(askResponse.sources)} />);
  expect(screen.getByRole("button", { name: /Source 1/ })).toBeInTheDocument();
  expect(screen.getByText(/RAG retrieves passages/)).toBeInTheDocument();
});

test("a view that throws shows a recoverable message instead of a blank page", async () => {
  const { ErrorBoundary } = await import("./ErrorBoundary");
  const Boom = () => {
    throw new Error("boom");
  };
  vi.spyOn(console, "error").mockImplementation(() => {});
  render(<ErrorBoundary><Boom /></ErrorBoundary>);
  expect(screen.getByRole("alert")).toHaveTextContent("Something went wrong");
});

test("a sentence stating a figure its source lacks is flagged, and the summary counts it", () => {
  const data = {
    ...askResponse,
    claims: [
      { text: "GPT-3 has 350 billion parameters.", markers: [1], status: "cited" as const, support: "not_supported" as const,
        support_method: "numbers" as const, unsupported_numbers: ["350"] },
      { text: "It is autoregressive.", markers: [1], status: "cited" as const, support: "unknown" as const },
    ],
    citation_check: { verification: "local_checks" as const, valid_markers: 1, removed_markers: [], uncited_claims: 0,
      supported_claims: 0, not_supported_claims: 1, unknown_claims: 1 },
  };
  render(<AnswerView data={data} sources={toViews(askResponse.sources)} />);
  const flagged = screen.getByText("GPT-3 has 350 billion parameters.");
  expect(flagged).toHaveClass("claim", "unsupported");
  expect(flagged).toHaveAttribute("title", expect.stringContaining("Not in the cited source: 350"));
  expect(screen.getByText("1 sentence states a figure not found in the cited source")).toBeInTheDocument();
});
