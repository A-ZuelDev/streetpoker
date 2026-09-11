import type { CardSuit, CardView } from './table.types';

const suitSymbols: Record<CardSuit, string> = {
  clubs: '♣',
  diamonds: '♦',
  hearts: '♥',
  spades: '♠',
};

interface PlayingCardProps {
  identity: CardView | null;
  hidden?: boolean;
  emphasis?: 'board' | 'hero' | 'opponent';
  visualTheme?: 'classic';
}

export function PlayingCard({
  identity,
  hidden = false,
  emphasis = 'board',
  visualTheme = 'classic',
}: PlayingCardProps) {
  const presentationClasses = `playing-card--${emphasis} playing-card--theme-${visualTheme}`;

  if (hidden) {
    return (
      <span
        className={`playing-card playing-card--back ${presentationClasses}`}
        data-card-theme={visualTheme}
        role="img"
        aria-label="Concealed card"
      >
        <span className="playing-card__back-mark" aria-hidden="true">
          S
        </span>
      </span>
    );
  }

  if (identity === null) {
    return (
      <span
        className={`playing-card playing-card--slot ${presentationClasses}`}
        data-card-theme={visualTheme}
        aria-hidden="true"
      />
    );
  }

  const symbol = suitSymbols[identity.suit];

  return (
    <span
      className={`playing-card playing-card--suit-${identity.suit} ${presentationClasses}`}
      data-card-theme={visualTheme}
      data-suit={identity.suit}
      role="img"
      aria-label={`${identity.rank} of ${identity.suit}`}
    >
      <span className="playing-card__rank">{identity.rank}</span>
      <span className="playing-card__suit" aria-hidden="true">
        {symbol}
      </span>
      <span className="playing-card__pip" aria-hidden="true">
        {symbol}
      </span>
    </span>
  );
}
