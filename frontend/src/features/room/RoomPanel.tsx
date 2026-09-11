import { useState, type FormEvent, type ReactNode } from 'react';

import { formatChips } from '../table/formatChips';
import type { ChatMessageView, RoomPanelView } from '../table/table.types';

interface RoomPanelProps {
  panel: RoomPanelView;
  chat: readonly ChatMessageView[];
  isOpen: boolean;
  onToggle: () => void;
}

interface CollapsibleRoomSectionProps {
  id: string;
  title: string;
  meta?: string;
  className?: string;
  defaultOpen?: boolean;
  children: ReactNode;
}

function CollapsibleRoomSection({
  id,
  title,
  meta,
  className = '',
  defaultOpen = true,
  children,
}: CollapsibleRoomSectionProps) {
  const [isOpen, setIsOpen] = useState(defaultOpen);
  const contentId = `${id}-content`;

  return (
    <section
      className={`room-panel__section ${className}`.trim()}
      aria-labelledby={`${id}-heading`}
    >
      <div className="room-panel__section-heading">
        <h2 id={`${id}-heading`}>{title}</h2>
        <span className="room-panel__section-heading-meta">
          {meta === undefined ? null : <span>{meta}</span>}
          <button
            type="button"
            aria-expanded={isOpen}
            aria-controls={contentId}
            aria-label={`${isOpen ? 'Collapse' : 'Expand'} ${title} section`}
            onClick={() => setIsOpen((current) => !current)}
          >
            <span className="room-panel__section-chevron" aria-hidden="true">
              {isOpen ? '-' : '+'}
            </span>
          </button>
        </span>
      </div>
      {isOpen ? (
        <div className="room-panel__section-content" id={contentId}>
          {children}
        </div>
      ) : null}
    </section>
  );
}

export function RoomPanel({ panel, chat, isOpen, onToggle }: RoomPanelProps) {
  const requestCount = panel.seatRequests.length;
  const [draft, setDraft] = useState('');
  const [messages, setMessages] = useState<readonly ChatMessageView[]>(chat);

  const submitDemoMessage = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const message = draft.trim();

    if (message.length === 0) {
      return;
    }

    setMessages((current) => [
      ...current,
      {
        id: `local-demo-${current.length}`,
        sender: 'You',
        message,
        timestamp: 'Now',
      },
    ]);
    setDraft('');
  };

  return (
    <aside
      className={`room-panel room-panel--${isOpen ? 'open' : 'closed'}`}
      aria-label="Room tools"
    >
      <button
        className="room-panel__toggle"
        type="button"
        aria-expanded={isOpen}
        aria-label={`${isOpen ? 'Collapse' : 'Expand'} room panel`}
        onClick={onToggle}
      >
        <span aria-hidden="true">{isOpen ? 'Hide' : 'Room'}</span>
      </button>

      {isOpen ? (
        <div className="room-panel__body">
          <CollapsibleRoomSection
            id="members"
            title="Members"
            meta={String(panel.members.length)}
          >
            <ul className="member-list" aria-label="Room members">
              {panel.members.map((member) => (
                <li key={member.nickname} className="member-row">
                  <span className="member-row__avatar" aria-hidden="true">
                    {member.nickname.slice(0, 1)}
                  </span>
                  <span className="member-row__identity">
                    <span>
                      {member.nickname}
                      {member.isHost ? <small>Host</small> : null}
                    </span>
                    <small>{member.status}</small>
                  </span>
                  <span className="member-row__stack">
                    {member.stack === null ? '-' : formatChips(member.stack)}
                  </span>
                </li>
              ))}
            </ul>
          </CollapsibleRoomSection>

          <CollapsibleRoomSection
            id="seat-requests"
            title="Seat requests"
            meta={String(requestCount)}
            defaultOpen={requestCount > 0}
          >
            {requestCount === 0 ? (
              <p className="room-panel__empty">No pending requests</p>
            ) : (
              <ul className="request-list">
                {panel.seatRequests.map((request) => (
                  <li key={`${request.nickname}-${request.seatIndex}`}>
                    <div>
                      <strong>{request.nickname}</strong>
                      <span>Seat {request.seatIndex + 1}</span>
                    </div>
                    <div className="request-actions">
                      <button type="button" title="Demo only">
                        Approve
                      </button>
                      <button
                        className="request-actions__reject"
                        type="button"
                        title="Demo only"
                      >
                        Reject
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </CollapsibleRoomSection>

          <CollapsibleRoomSection
            id="chat"
            title="Chat"
            meta="Demo"
            className="room-panel__chat"
          >
            <ol className="chat-list" aria-label="Recent chat messages">
              {messages.map((chatMessage) => (
                <li key={chatMessage.id} className="chat-message">
                  <div className="chat-message__meta">
                    <strong>{chatMessage.sender}</strong>
                    <time>{chatMessage.timestamp}</time>
                  </div>
                  <p>{chatMessage.message}</p>
                </li>
              ))}
            </ol>
            <form className="chat-compose" onSubmit={submitDemoMessage}>
              <label className="sr-only" htmlFor="demo-chat-message">
                Chat message
              </label>
              <input
                id="demo-chat-message"
                type="text"
                value={draft}
                placeholder="Say something..."
                autoComplete="off"
                onChange={(event) => setDraft(event.currentTarget.value)}
              />
              <button type="submit" disabled={draft.trim().length === 0}>
                Send
              </button>
            </form>
          </CollapsibleRoomSection>

          <CollapsibleRoomSection
            id="host-controls"
            title="Host controls"
            className="room-panel__host-controls"
            defaultOpen={false}
          >
            <div className="host-controls-grid">
              <button type="button" disabled title="Demo only">
                Room settings
              </button>
              <button
                className="room-panel__danger"
                type="button"
                disabled
                title="Demo only"
              >
                Close room
              </button>
            </div>
          </CollapsibleRoomSection>
        </div>
      ) : null}
    </aside>
  );
}
