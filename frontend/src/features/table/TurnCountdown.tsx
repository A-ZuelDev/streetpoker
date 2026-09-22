import { useEffect, useState } from 'react';

interface TurnCountdownProps {
  deadlineUnixMs: number | null;
  timerRemainingMs: number;
  timebankRemainingMs: number;
  timebankTotalMs: number;
  usingTimebank: boolean;
  paused: boolean;
  refillAmountMs: number;
  refillHandsRemaining: number | null;
}

export function TurnCountdown({
  deadlineUnixMs,
  timerRemainingMs,
  timebankRemainingMs,
  timebankTotalMs,
  usingTimebank,
  paused,
  refillAmountMs,
  refillHandsRemaining,
}: TurnCountdownProps) {
  const [nowUnixMs, setNowUnixMs] = useState(Date.now);

  useEffect(() => {
    if (paused || deadlineUnixMs === null) {
      return undefined;
    }
    const interval = window.setInterval(() => setNowUnixMs(Date.now()), 250);
    return () => window.clearInterval(interval);
  }, [deadlineUnixMs, paused]);

  const phaseRemainingMs =
    paused || deadlineUnixMs === null
      ? timerRemainingMs
      : Math.max(0, deadlineUnixMs - nowUnixMs);
  const baseRemainingMs = usingTimebank ? 0 : phaseRemainingMs;
  const bankRemainingMs = usingTimebank
    ? Math.min(timebankRemainingMs, phaseRemainingMs)
    : timebankRemainingMs;
  const bankUsedMs = Math.max(0, timebankTotalMs - bankRemainingMs);
  const baseSeconds = Math.ceil(baseRemainingMs / 1_000);
  const bankSeconds = Math.ceil(bankRemainingMs / 1_000);
  const usedSeconds = Math.floor(bankUsedMs / 1_000);
  const refillText =
    refillAmountMs === 0 || refillHandsRemaining === null
      ? 'No automatic refill'
      : `+${Math.floor(refillAmountMs / 1_000)}s in ${refillHandsRemaining} ${refillHandsRemaining === 1 ? 'hand' : 'hands'}`;

  return (
    <div
      className={`turn-countdown${paused ? ' turn-countdown--paused' : ''}`}
      role="timer"
      aria-label="Authoritative action timer and time bank"
      aria-live="off"
    >
      <span className="turn-countdown__phase">
        {paused ? 'Game paused' : usingTimebank ? 'Time bank' : 'Action'}
      </span>
      <strong className="turn-countdown__remaining">
        {usingTimebank ? bankSeconds : baseSeconds}s
      </strong>
      <small className="turn-countdown__summary">
        Action {baseSeconds}s · Bank {bankSeconds}s left / {usedSeconds}s used
      </small>
      {timebankTotalMs > 0 ? (
        <progress
          className="turn-countdown__bank-meter"
          aria-label="Time bank remaining"
          max={timebankTotalMs}
          value={bankRemainingMs}
        />
      ) : null}
      <small className="turn-countdown__refill">{refillText}</small>
      {!paused && phaseRemainingMs === 0 ? (
        <small className="turn-countdown__sync">Awaiting table update</small>
      ) : null}
    </div>
  );
}
