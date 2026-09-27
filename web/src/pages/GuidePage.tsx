/* Guide: plain-English explainer — what BRAWLHOUSE is, how hiring,
 * entering, battles, betting, seasons, and notifications work. */

import { Link } from 'react-router-dom';

function Section({
  n,
  title,
  children,
}: {
  n: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section
      style={{
        border: '1px solid var(--line)',
        borderRadius: 12,
        padding: '20px 22px',
        marginBottom: 16,
        background: 'var(--panel)',
      }}
    >
      <h2 style={{ margin: '0 0 10px', fontSize: 17 }}>
        <span style={{ color: 'var(--accent)', marginRight: 8 }}>{n}</span>
        {title}
      </h2>
      <div style={{ fontSize: 14, lineHeight: 1.65, color: 'var(--text)' }}>
        {children}
      </div>
    </section>
  );
}

export default function GuidePage() {
  return (
    <div style={{ maxWidth: 760, margin: '0 auto' }}>
      <h1 style={{ fontSize: 24, marginBottom: 6 }}>How BRAWLHOUSE works</h1>
      <p className="muted" style={{ marginBottom: 24, fontSize: 14 }}>
        Everything a player needs to know, in plain words. No jargon, no
        surprises.
      </p>

      <Section n="01" title="What is this?">
        <p>
          BRAWLHOUSE is an arena where AI fighter bots battle live in front of
          you. There are five house fighters — IRON-1, HAWK-2, AEGIS-4,
          JACKAL-5, and WASP-6 — each with its own fighting style. You can
          also create your own fighter, hire a house bot to fight for you, or
          just watch and bet on the winner.
        </p>
      </Section>

      <Section n="02" title="Get a fighter (Born)">
        <p>
          Go to the <Link to="/born">Born</Link> page to create your own
          fighter. It costs <b>0.1 SOL</b>. Your fighter gets a random rarity
          (Common, Rare, Epic, or Legendary) which boosts its stats, plus a
          unique ID and an ownership certificate. It is yours — it lives in
          your <Link to="/my-fighters">My Fighters</Link> page.
        </p>
      </Section>

      <Section n="03" title="No fighter? Hire a house bot">
        <p>
          If you don't have your own fighter, you can hire one of the house
          bots for a single official battle. Head to the{' '}
          <Link to="/queue">Queue</Link> page while the entry window is open
          and pick a bot. The hire fee depends on how strong the bot is —
          stronger bots cost more.
        </p>
        <p>
          The hired bot fights under your wallet. <b>If it wins, you get the
          battle prize.</b> If it loses, the fee is gone — that's the game.
          You can only have one hire at a time, and each bot can only be
          hired by one player per battle.
        </p>
      </Section>

      <Section n="04" title="Entering battles (Queue)">
        <p>
          Official battles run on a schedule — roughly every few minutes —
          and alternate between <b>Duels</b> (1v1) and <b>Royales</b> (four
          fighters, free-for-all). When the entry window opens, enter your
          fighter from the <Link to="/queue">Queue</Link> page. Entry costs{' '}
          <b>0.02 SOL</b>.
        </p>
        <p>
          Fighters are drawn from the queue with longest-wait priority, so
          nobody waits forever. Hired house bots get guaranteed slots. If the
          queue is short, unhired house bots fill in so there's always a
          fight — but they never take prize money.
        </p>
      </Section>

      <Section n="05" title="The battle and the prize">
        <p>
          Once drawn, the battle plays out live in the{' '}
          <Link to="/arena">Arena</Link>. Watch the HP bars, the eliminations
          feed, and the combat log.
        </p>
        <p>
          The winner takes the <b>battle prize pool</b>, built from entry
          fees (80% of each entry fee goes to the battle pool, 20% to the
          season pool). The prize goes to the winning fighter's owner — or to
          you, if your hired bot won.
        </p>
      </Section>

      <Section n="06" title="Betting">
        <p>
          While a battle is in the pre-game window, you can bet on which
          fighter wins. It's a parimutuel pool: all bets go in, and winners
          split the pool proportionally. Betting closes the moment combat
          starts. Exhibition fights never take bets.
        </p>
      </Section>

      <Section n="07" title="Seasons">
        <p>
          Every 15 days a season ends and the top three fighters on the{' '}
          <Link to="/season">Season</Link> leaderboard split the season pool —{' '}
          <b>60% / 25% / 15%</b>. The season pool grows from half of every
          Born fee and 20% of every battle entry fee. Only official battles
          count toward season standings.
        </p>
      </Section>

      <Section n="08" title="Notifications">
        <p>
          The bell in the top bar keeps your personal feed: your hired bot's
          result, your fighter's result and winnings. It only lights up after
          you've seen the battle finish — no spoilers while you're still
          watching. Click the bell to read; opening it marks everything read.
        </p>
      </Section>

      <Section n="09" title="Money and safety">
        <p>
          Hire fees, entry fees, and Born fees are real SOL when live mode is
          on. A cut of every fee funds $BRAWL buybacks and burns. Payouts go
          to the wallet that entered or hired — double-check your connected
          wallet before you pay for anything.
        </p>
      </Section>
    </div>
  );
}
