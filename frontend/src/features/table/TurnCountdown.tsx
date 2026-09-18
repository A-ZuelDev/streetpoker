import { useEffect, useState } from 'react';

interface TurnCountdownProps {
  deadlineUnixMs: number;
  usingTimebank: boolean;
}

export function TurnCountdown({
  deadlineUnixMs,
  usingTimebank,
}: TurnCountdownProps) {
  const [nowUnixMs, setNowUnixMs] = useState(Date.now);

  useEffect(() => {
    const interval = window.setInterval(() => setNowUnixMs(Date.now()), 250);
    return () => window.clearInterval(interval);
  }, []);

  const secondsRemaining = Math.ceil(
    Math.max(0, deadlineUnixMs - nowUnixMs) / 1000,
  );

  return (
    <div
      className="turn-countdown"
      role="timer"
      aria-label={
        usingTimebank
          ? 'Approximate timebank time remaining'
          : 'Approximate turn time remaining'
      }
      aria-live="off"
    >
      <span>{usingTimebank ? 'Timebank' : 'Approx. turn time'}</span>
      <strong>{secondsRemaining}s</strong>
      {secondsRemaining === 0 ? <small>Awaiting table update</small> : null}
    </div>
  );
}
