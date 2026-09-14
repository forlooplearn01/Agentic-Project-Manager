export default function TypingIndicator() {
  return (
    <div className="flex w-full justify-start">
      <div className="flex items-center gap-1.5 rounded-2xl border border-line bg-surface px-4 py-3.5 shadow-sm">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="pulse-dot h-1.5 w-1.5 rounded-full bg-gradient-to-br from-accent-from to-accent-to"
            style={{ animationDelay: `${i * 0.15}s` }}
          />
        ))}
      </div>
    </div>
  );
}
