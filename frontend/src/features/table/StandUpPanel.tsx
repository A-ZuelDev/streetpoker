import { formatChips } from './formatChips';
import type { StandUpView } from './table.types';

export function StandUpPanel({ standUp }: { standUp: StandUpView | null }) {
  if (standUp === null || (!standUp.enabled && standUp.lastResult === null)) {
    return null;
  }

  const active = standUp.activeRound;
  const result = standUp.lastResult;

  return (
    <aside className="stand-up-panel" aria-label="Stand-Up side game">
      <div className="stand-up-panel__heading">
        <strong>Stand-Up</strong>
        <span>{formatChips(standUp.penaltyPerRecipientChips)} per player</span>
      </div>
      {active !== null ? (
        <p>
          <b>{active.atRiskSeatNumbers.length} at risk</b>
          <span aria-hidden="true"> · </span>
          <span>{active.clearedSeatNumbers.length} cleared</span>
        </p>
      ) : result?.kind === 'resolution' ? (
        <div className="stand-up-panel__result" role="status">
          <p>
            <b>Seat {result.squidSeatNumber} is the squid</b>
            <span>
              Paid {formatChips(result.actualTotal)}
              {result.shortfall > 0
                ? ` of ${formatChips(result.intendedTotal)}`
                : ''}
            </span>
          </p>
          <ul aria-label="Stand-Up payouts">
            {result.transfers.map((transfer) => (
              <li key={transfer.toSeatNumber}>
                Seat {transfer.toSeatNumber} +{formatChips(transfer.chips)}
              </li>
            ))}
          </ul>
        </div>
      ) : result?.kind === 'cancellation' ? (
        <p className="stand-up-panel__cancelled" role="status">
          <b>Round cancelled</b>
          <span>{result.reason}</span>
        </p>
      ) : (
        <p>
          <b>Waiting for the next hand</b>
          <span>The next dealt hand starts a round.</span>
        </p>
      )}
    </aside>
  );
}
