import { formatChips } from './formatChips';
import { PlayingCard } from './PlayingCard';
import type { OccupiedSeatView, SeatView } from './table.types';

interface PlayerPodProps {
  seat: SeatView;
  interactiveDemo: boolean;
  onRequestSeat?: (seatIndex: number) => void;
}

const stateLabels: Record<OccupiedSeatView['state'], string> = {
  active: 'In hand',
  seated: 'Seated',
  folded: 'Folded',
  'all-in': 'All in',
  'sitting-out': 'Sitting out',
  'not-in-hand': 'Not in hand',
};

function SeatCards({ seat }: { seat: OccupiedSeatView }) {
  if (seat.cards === null) {
    return null;
  }

  if (seat.cards === 'concealed') {
    return (
      <div className="seat-cards seat-cards--concealed">
        <PlayingCard identity={null} hidden emphasis="opponent" />
        <PlayingCard identity={null} hidden emphasis="opponent" />
      </div>
    );
  }

  return (
    <div
      className="seat-cards seat-cards--hero"
      data-testid={seat.isHero ? 'hero-hole-cards' : undefined}
    >
      {seat.cards.map((card) => (
        <PlayingCard
          key={`${card.rank}-${card.suit}`}
          identity={card}
          emphasis={seat.isHero ? 'hero' : 'opponent'}
        />
      ))}
    </div>
  );
}

export function PlayerPod({
  seat,
  interactiveDemo,
  onRequestSeat,
}: PlayerPodProps) {
  if (seat.kind === 'empty') {
    return (
      <div
        className={`table-seat table-seat--${seat.position}`}
        data-seat-index={seat.seatIndex}
      >
        {interactiveDemo || seat.requestLabel !== undefined ? (
          <button
            className="player-pod player-pod--empty"
            type="button"
            aria-label={
              interactiveDemo
                ? `Request empty seat ${seat.seatIndex + 1}`
                : seat.requestLabel
            }
            disabled={!interactiveDemo && !seat.canRequest}
            {...(interactiveDemo ? { title: 'Demo only' } : {})}
            onClick={() => onRequestSeat?.(seat.seatIndex)}
          >
            <span className="player-pod__empty-icon" aria-hidden="true">
              +
            </span>
            <span>{seat.requested ? 'Requested' : 'Open seat'}</span>
          </button>
        ) : (
          <div
            className="player-pod player-pod--empty"
            aria-label={`Empty seat ${seat.seatIndex + 1}`}
          >
            <span className="player-pod__empty-icon" aria-hidden="true">
              +
            </span>
            <span>Open seat</span>
          </div>
        )}
      </div>
    );
  }

  const status = seat.isActing
    ? seat.isHero
      ? 'Your turn'
      : 'Acting'
    : stateLabels[seat.state];

  return (
    <article
      className={`table-seat table-seat--${seat.position} ${seat.isHero ? 'table-seat--hero' : ''} table-seat--state-${seat.state}`}
      data-seat-index={seat.seatIndex}
      aria-label={`Seat ${seat.seatIndex + 1}: ${seat.nickname}`}
    >
      <SeatCards seat={seat} />

      <div
        className={`player-pod ${seat.isActing ? 'player-pod--acting' : ''} ${seat.isHero ? 'player-pod--hero' : ''}`}
        data-testid={seat.isHero ? 'hero-player-pod' : undefined}
      >
        <div className="player-pod__identity">
          <span className="player-pod__name">{seat.nickname}</span>
          {seat.isHero ? <span className="player-pod__you">You</span> : null}
        </div>
        <span className="player-pod__stack">{formatChips(seat.stack)}</span>
        <span className="player-pod__status">{status}</span>

        <span className="player-pod__markers" aria-label="Seat markers">
          {seat.isDealer ? (
            <span className="seat-marker seat-marker--dealer" title="Dealer">
              D
            </span>
          ) : null}
          {seat.blind === 'small-blind' ? (
            <span className="seat-marker" title="Small blind">
              SB
            </span>
          ) : null}
          {seat.blind === 'big-blind' ? (
            <span className="seat-marker" title="Big blind">
              BB
            </span>
          ) : null}
        </span>
      </div>
    </article>
  );
}
