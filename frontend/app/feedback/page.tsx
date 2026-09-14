import { IconInbox } from "@/components/icons";

export default function FeedbackPage() {
  return (
    <main className="mx-auto flex max-w-3xl flex-col items-center justify-center px-6 py-24 text-center">
      <div className="glass-panel flex flex-col items-center gap-3 rounded-2xl p-8">
        <IconInbox className="h-6 w-6 text-text-faint" />
        <h1 className="text-base font-semibold text-text-primary">Feedback</h1>
        <p className="max-w-sm text-[13px] text-text-muted">An in-app feedback form is coming soon.</p>
      </div>
    </main>
  );
}
