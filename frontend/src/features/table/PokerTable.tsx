import { formatChips } from './formatChips';
import { PlayerPod } from './PlayerPod';
import { PlayingCard } from './PlayingCard';
import type { SeatView, TableDemoView } from './table.types';

interface PokerTableProps {
  table: TableDemoView;
}

const boardSlots = [0, 1, 2, 3, 4] as const;

function ContributionMarker({ seat }: { seat: SeatView }) {
  if (seat.kind === 'empty' || seat.contribution === null) {
    return null;
  }

  return (
    <span
      className={`seat-contribution contribution-position--${seat.position}`}
      aria-label={`${seat.nickname} current contribution ${formatChips(seat.contribution)}`}
      data-seat-contribution="true"
      data-contribution-position={seat.position}
    >
      <span className="chip-stack" aria-hidden="true">
        <span />
        <span />
        <span />
      </span>
      <span className="sr-only">Current contribution </span>
      <span className="seat-contribution__amount">
        {formatChips(seat.contribution)}
      </span>
    </span>
  );
}

export function PokerTable({ table }: PokerTableProps) {
  return (
    <section className="poker-stage" aria-label="Six-max poker table">
      <div className="poker-table">
        <div className="poker-table__rail" aria-hidden="true" />
        <div className="poker-table__felt-line" aria-hidden="true" />
        <div className="poker-table__felt" data-testid="poker-felt">
          {table.seats.map((seat) => (
            <ContributionMarker key={seat.seatIndex} seat={seat} />
          ))}
        </div>
      </div>

      <div className="table-center">
        <span className="table-center__street">{table.street}</span>
        <div className="board-cards" aria-label="Community board">
          {boardSlots.map((index) => (
            <PlayingCard
              key={index}
              identity={table.board[index] ?? null}
              emphasis="board"
            />
          ))}
        </div>
        <div
          className="pot-display"
          aria-label={`Pot ${formatChips(table.pot)}`}
        >
          <span className="pot-display__label">Pot</span>
          <span className="pot-display__amount">{formatChips(table.pot)}</span>
        </div>
      </div>

      {table.seats.map((seat) => (
        <PlayerPod key={seat.seatIndex} seat={seat} />
      ))}
    </section>
  );
}
