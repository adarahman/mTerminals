import { ScrollView, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { MarketContextBar } from '../../src/components/MarketContextBar';
import { useMarketData } from '../../src/hooks/useMarketData';

export default function MarketScreen() {
  const { payload, connected, error } = useMarketData();
  const market: any = payload?.market ?? {};

  const indices = uniqueIndices(market.allIndices);
  const velocity = Array.isArray(market.oiVelocity)
    ? market.oiVelocity
    : [];

  return (
    <SafeAreaView
      style={{ flex: 1, backgroundColor: '#0b0d10' }}
      edges={['top']}
    >
      <MarketContextBar />

      <ScrollView
        contentContainerStyle={{
          padding: 16,
          paddingBottom: 42,
          gap: 14,
        }}
      >
        <View
          style={{
            flexDirection: 'row',
            justifyContent: 'space-between',
            alignItems: 'center',
          }}
        >
          <Text
            style={{
              color: '#ffffff',
              fontSize: 26,
              fontWeight: '800',
            }}
          >
            Market
          </Text>

          <Text
            style={{
              color: connected ? '#63d297' : '#ef7777',
              fontWeight: '700',
              fontSize: 12,
            }}
          >
            {connected ? '● LIVE' : '● OFFLINE'}
          </Text>
        </View>

        {error ? (
          <Card>
            <Text style={{ color: '#ef7777' }}>{error}</Text>
          </Card>
        ) : null}

        <Card>
          <SectionTitle>INDICES</SectionTitle>

          <View style={{ marginTop: 8 }}>
            {indices.map((item: any) => (
              <IndexRow
                key={item.BackendSymbol || item.Symbol}
                symbol={item.Symbol}
                price={item['Last Price']}
                change={item.Change}
                pct={item['% Change']}
              />
            ))}
          </View>
        </Card>

        <Card>
          <View
            style={{
              flexDirection: 'row',
              justifyContent: 'space-between',
              alignItems: 'center',
            }}
          >
            <SectionTitle>FUTURES POSITIONING</SectionTitle>

            <Text
              style={{
                color: '#7f8a99',
                fontSize: 11,
                fontWeight: '700',
              }}
            >
              {market.futuresExpiry || '—'}
            </Text>
          </View>

          <Text
            style={{
              color: signalColor(market.futSignal),
              fontSize: 21,
              fontWeight: '800',
              marginTop: 10,
            }}
          >
            {market.futSignal || '—'}
          </Text>

          <View style={styles.metricGrid}>
            <Metric
              label="FUTURE"
              value={formatNumber(market.future, 2)}
              sub={changeText(
                market.futureChange,
                market.futureChgPct,
              )}
              direction={direction(market.futureChange)}
            />

            <Metric
              label="BASIS"
              value={signedNumber(market.basis)}
            />

            <Metric
              label="FUT OI"
              value={compactNumber(market.futOi)}
            />

            <Metric
              label="OI CHANGE"
              value={signedCompactNumber(market.futOiChg)}
              sub={percentText(market.futOiChgPct)}
              direction={direction(market.futOiChg)}
            />
          </View>
        </Card>

        <Card>
          <SectionTitle>OI FLOW / VELOCITY</SectionTitle>

          <Text
            style={{
              color: '#7f8a99',
              fontSize: 12,
              marginTop: 6,
              lineHeight: 17,
            }}
          >
            Change in open interest by strike across the backend velocity
            windows.
          </Text>

          <View style={{ marginTop: 12, gap: 10 }}>
            {velocity.length ? (
              velocity.map((window: any) => (
                <VelocityWindow
                  key={String(window.window)}
                  window={window}
                  atm={market.atm}
                />
              ))
            ) : (
              <Text style={{ color: '#8c96a5' }}>
                No OI velocity data.
              </Text>
            )}
          </View>
        </Card>

        <Card>
          <SectionTitle>MARKET STRUCTURE</SectionTitle>

          <View style={styles.metricGrid}>
            <Metric
              label="SPOT"
              value={formatNumber(market.spot, 2)}
            />

            <Metric
              label="ATM"
              value={formatNumber(market.atm, 0)}
            />

            <Metric
              label="MAX PAIN"
              value={formatNumber(market.maxPain, 0)}
            />

            <Metric
              label="PCR"
              value={formatNumber(market.totalPCR, 2)}
              sub={market.pcrSentiment || ''}
            />
          </View>

          <View style={styles.metricGrid}>
            <Metric
              label="CE WALL"
              value={formatNumber(market.ceWall, 0)}
            />

            <Metric
              label="PE WALL"
              value={formatNumber(market.peWall, 0)}
            />

            <Metric
              label="OI CHG PCR"
              value={formatNumber(market.oiChgPCR, 2)}
            />

            <Metric
              label="VIX"
              value={formatNumber(market.indiaVix, 2)}
              sub={percentText(market.indiaVixChgPct)}
              direction={direction(market.indiaVixChgPct)}
            />
          </View>
        </Card>

        <Text
          style={{
            color: '#58616d',
            textAlign: 'center',
            fontSize: 11,
          }}
        >
          {market.dataSource
            ? `Source: ${market.dataSource}`
            : 'mTerminals'}
        </Text>
      </ScrollView>
    </SafeAreaView>
  );
}

function VelocityWindow({
  window,
  atm,
}: {
  window: any;
  atm: any;
}) {
  const rows = Array.isArray(window.rows) ? window.rows : [];

  const activeRows = rows.filter(
    (row: any) =>
      Number(row.ceDOI || 0) !== 0 ||
      Number(row.peDOI || 0) !== 0,
  );

  const strongest = [...activeRows]
    .sort(
      (a, b) =>
        velocityStrength(b) - velocityStrength(a),
    )
    .slice(0, 3);

  const nearest = [...rows]
    .sort(
      (a, b) =>
        Math.abs(Number(a.strike) - Number(atm)) -
        Math.abs(Number(b.strike) - Number(atm)),
    )
    .slice(0, 3);

  const shown = strongest.length ? strongest : nearest;

  return (
    <View
      style={{
        backgroundColor: '#101419',
        borderRadius: 13,
        padding: 13,
      }}
    >
      <View
        style={{
          flexDirection: 'row',
          justifyContent: 'space-between',
          alignItems: 'center',
        }}
      >
        <Text
          style={{
            color: '#ffffff',
            fontSize: 15,
            fontWeight: '800',
          }}
        >
          {window.window}m
        </Text>

        <Text
          style={{
            color: activeRows.length
              ? '#63d297'
              : '#7f8a99',
            fontSize: 11,
            fontWeight: '700',
          }}
        >
          {activeRows.length
            ? `${activeRows.length} active strikes`
            : 'No OI delta yet'}
        </Text>
      </View>

      <View style={{ marginTop: 8 }}>
        {shown.map((row: any) => (
          <View
            key={String(row.strike)}
            style={{
              flexDirection: 'row',
              alignItems: 'center',
              paddingVertical: 6,
              borderTopWidth: 1,
              borderTopColor: '#20252c',
            }}
          >
            <Text
              style={{
                color:
                  Number(row.strike) === Number(atm)
                    ? '#ffffff'
                    : '#a9b1bd',
                fontWeight:
                  Number(row.strike) === Number(atm)
                    ? '800'
                    : '600',
                width: 62,
              }}
            >
              {row.strike}
            </Text>

            <View style={{ flex: 1 }}>
              <VelocityBar
                value={Number(row.ceDOI || 0)}
                max={maxVelocity(rows)}
              />

              <Text
                style={{
                  color: '#ef7777',
                  fontSize: 10,
                  marginTop: 2,
                }}
              >
                CE ΔOI {signedCompactNumber(row.ceDOI)}
              </Text>
            </View>

            <View style={{ width: 10 }} />

            <View style={{ flex: 1 }}>
              <VelocityBar
                value={Number(row.peDOI || 0)}
                max={maxVelocity(rows)}
              />

              <Text
                style={{
                  color: '#63d297',
                  fontSize: 10,
                  marginTop: 2,
                }}
              >
                PE ΔOI {signedCompactNumber(row.peDOI)}
              </Text>
            </View>
          </View>
        ))}
      </View>
    </View>
  );
}

function VelocityBar({
  value,
  max,
}: {
  value: number;
  max: number;
}) {
  const width =
    max > 0
      ? Math.max(
          value === 0 ? 0 : 3,
          Math.min(100, (Math.abs(value) / max) * 100),
        )
      : 0;

  return (
    <View
      style={{
        height: 5,
        backgroundColor: '#242a32',
        borderRadius: 4,
        overflow: 'hidden',
      }}
    >
      <View
        style={{
          width: `${width}%`,
          height: '100%',
          backgroundColor:
            value > 0
              ? '#63d297'
              : value < 0
                ? '#ef7777'
                : '#59616d',
        }}
      />
    </View>
  );
}

function IndexRow({
  symbol,
  price,
  change,
  pct,
}: {
  symbol: string;
  price: any;
  change: any;
  pct: any;
}) {
  const dir = direction(change);

  return (
    <View
      style={{
        flexDirection: 'row',
        alignItems: 'center',
        paddingVertical: 10,
        borderTopWidth: 1,
        borderTopColor: '#232830',
      }}
    >
      <Text
        style={{
          flex: 1,
          color: '#ffffff',
          fontWeight: '700',
        }}
      >
        {symbol}
      </Text>

      <Text
        style={{
          width: 105,
          color: '#ffffff',
          textAlign: 'right',
          fontWeight: '700',
        }}
      >
        {formatNumber(price, 2)}
      </Text>

      <Text
        style={{
          width: 82,
          color:
            dir === 'up'
              ? '#63d297'
              : dir === 'down'
                ? '#ef7777'
                : '#8c96a5',
          textAlign: 'right',
          fontWeight: '700',
        }}
      >
        {percentText(pct)}
      </Text>
    </View>
  );
}

function Card({ children }: { children: React.ReactNode }) {
  return (
    <View
      style={{
        backgroundColor: '#15191f',
        borderRadius: 16,
        padding: 16,
      }}
    >
      {children}
    </View>
  );
}

function SectionTitle({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <Text
      style={{
        color: '#7f8a99',
        fontSize: 11,
        fontWeight: '700',
        letterSpacing: 0.8,
      }}
    >
      {children}
    </Text>
  );
}

function Metric({
  label,
  value,
  sub,
  direction: dir = 'flat',
}: {
  label: string;
  value: string;
  sub?: string;
  direction?: 'up' | 'down' | 'flat';
}) {
  return (
    <View
      style={{
        width: '48%',
        backgroundColor: '#101419',
        borderRadius: 12,
        padding: 12,
      }}
    >
      <Text
        style={{
          color: '#7f8a99',
          fontSize: 10,
          fontWeight: '700',
        }}
      >
        {label}
      </Text>

      <Text
        style={{
          color: '#ffffff',
          fontSize: 17,
          fontWeight: '800',
          marginTop: 5,
        }}
      >
        {value}
      </Text>

      {sub ? (
        <Text
          style={{
            color:
              dir === 'up'
                ? '#63d297'
                : dir === 'down'
                  ? '#ef7777'
                  : '#8c96a5',
            fontSize: 11,
            marginTop: 3,
          }}
        >
          {sub}
        </Text>
      ) : null}
    </View>
  );
}

const styles = {
  metricGrid: {
    flexDirection: 'row' as const,
    flexWrap: 'wrap' as const,
    justifyContent: 'space-between' as const,
    gap: 10,
    marginTop: 12,
  },
};

function uniqueIndices(value: any): any[] {
  if (!Array.isArray(value)) return [];

  const seen = new Set<string>();

  return value.filter(item => {
    const key = String(
      item?.BackendSymbol || item?.Symbol || '',
    ).toUpperCase();

    if (!key || seen.has(key)) return false;

    seen.add(key);
    return true;
  });
}

function velocityStrength(row: any): number {
  return (
    Math.abs(Number(row?.ceDOI || 0)) +
    Math.abs(Number(row?.peDOI || 0))
  );
}

function maxVelocity(rows: any[]): number {
  return rows.reduce(
    (max, row) =>
      Math.max(
        max,
        Math.abs(Number(row?.ceDOI || 0)),
        Math.abs(Number(row?.peDOI || 0)),
      ),
    0,
  );
}

function formatNumber(value: any, decimals = 2): string {
  const number = Number(value);

  if (!Number.isFinite(number)) return '—';

  return number.toLocaleString('en-IN', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

function compactNumber(value: any): string {
  const number = Number(value);

  if (!Number.isFinite(number)) return '—';

  const abs = Math.abs(number);

  if (abs >= 10000000) {
    return `${(number / 10000000).toFixed(2)}Cr`;
  }

  if (abs >= 100000) {
    return `${(number / 100000).toFixed(2)}L`;
  }

  if (abs >= 1000) {
    return `${(number / 1000).toFixed(1)}K`;
  }

  return number.toFixed(0);
}

function signedCompactNumber(value: any): string {
  const number = Number(value);

  if (!Number.isFinite(number)) return '—';

  const formatted = compactNumber(Math.abs(number));

  if (number > 0) return `+${formatted}`;
  if (number < 0) return `-${formatted}`;

  return '0';
}

function signedNumber(value: any): string {
  const number = Number(value);

  if (!Number.isFinite(number)) return '—';

  return `${number >= 0 ? '+' : ''}${number.toFixed(2)}`;
}

function percentText(value: any): string {
  const number = Number(value);

  if (!Number.isFinite(number)) return '—';

  return `${number >= 0 ? '+' : ''}${number.toFixed(2)}%`;
}

function changeText(change: any, pct: any): string {
  const c = Number(change);
  const p = Number(pct);

  const left = Number.isFinite(c)
    ? `${c >= 0 ? '+' : ''}${c.toFixed(2)}`
    : '';

  const right = Number.isFinite(p)
    ? `${p >= 0 ? '+' : ''}${p.toFixed(2)}%`
    : '';

  return [left, right].filter(Boolean).join('  ');
}

function direction(
  value: any,
): 'up' | 'down' | 'flat' {
  const number = Number(value);

  if (!Number.isFinite(number) || number === 0) {
    return 'flat';
  }

  return number > 0 ? 'up' : 'down';
}

function signalColor(value: any): string {
  const text = String(value || '').toUpperCase();

  if (
    text.includes('LONG BUILD') ||
    text.includes('SHORT COVER')
  ) {
    return '#63d297';
  }

  if (
    text.includes('SHORT BUILD') ||
    text.includes('LONG UNWIND')
  ) {
    return '#ef7777';
  }

  return '#f2c96d';
}
