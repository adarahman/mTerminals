import { ScrollView, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { MarketContextBar } from '../../src/components/MarketContextBar';
import { useMarketData } from '../../src/hooks/useMarketData';

export default function HomeScreen() {
  const { payload, connected, error } = useMarketData();
  const market: any = payload?.market ?? {};

  const decision = readDecision(market.decision);
  const bias =
    textValue(market.compositeBias) ||
    textValue(market.spotBias) ||
    decision.bias ||
    '—';

  const confidence = readConfidence(market.decision);

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
          gap: 12,
        }}
      >
        <View
          style={{
            flexDirection: 'row',
            justifyContent: 'space-between',
            alignItems: 'center',
          }}
        >
          <View>
            <Text
              style={{
                color: '#ffffff',
                fontSize: 24,
                fontWeight: '800',
              }}
            >
              {market.symbolName || market.symbol || 'mTerminals'}
            </Text>

            <Text
              style={{
                color: '#7f8a99',
                marginTop: 3,
                fontSize: 12,
              }}
            >
              {market.expiry ? `Expiry ${market.expiry}` : ''}
            </Text>
          </View>

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
          <View style={styles.card}>
            <Text style={{ color: '#ef7777' }}>{error}</Text>
          </View>
        ) : null}

        <View style={styles.decisionCard}>
          <Text style={styles.label}>DECISION</Text>

          <Text
            style={{
              color: decisionColor(decision.action || bias),
              fontSize: 30,
              fontWeight: '900',
              marginTop: 8,
            }}
          >
            {decision.action || bias || 'WAIT'}
          </Text>

          <View
            style={{
              flexDirection: 'row',
              gap: 18,
              marginTop: 8,
              flexWrap: 'wrap',
            }}
          >
            <SmallText
              label="BIAS"
              value={bias}
            />

            <SmallText
              label="CONFIDENCE"
              value={
                confidence !== null
                  ? `${formatNumber(confidence, 0)}%`
                  : '—'
              }
            />

            <SmallText
              label="REGIME"
              value={textValue(market.marketRegime) || '—'}
            />
          </View>

          {decision.reason ? (
            <Text
              style={{
                color: '#9ba5b4',
                fontSize: 12,
                lineHeight: 18,
                marginTop: 12,
              }}
            >
              {decision.reason}
            </Text>
          ) : null}
        </View>

        <View style={styles.row}>
          <MetricCard
            title="SPOT"
            value={formatNumber(market.spot, 2)}
            secondary={changeText(
              market.spotChange,
              market.spotChgPct,
            )}
            direction={directionFromNumber(market.spotChange)}
          />

          <MetricCard
            title="FUTURES"
            value={formatNumber(market.future, 2)}
            secondary={changeText(
              market.futureChange,
              market.futureChgPct,
            )}
            direction={directionFromNumber(market.futureChange)}
          />
        </View>

        <View style={styles.row}>
          <MetricCard
            title="FUT BASIS"
            value={signedNumber(market.basis)}
          />

          <MetricCard
            title="INDIA VIX"
            value={formatNumber(market.indiaVix, 2)}
            secondary={percentText(market.indiaVixChgPct)}
            direction={directionFromNumber(market.indiaVixChgPct)}
          />
        </View>

        <View style={styles.row}>
          <MetricCard
            title="ATM"
            value={formatNumber(market.atm, 0)}
          />

          <MetricCard
            title="MAX PAIN"
            value={formatNumber(market.maxPain, 0)}
          />
        </View>

        <View style={styles.row}>
          <MetricCard
            title="TOTAL PCR"
            value={formatNumber(market.totalPCR, 2)}
            secondary={textValue(market.pcrSentiment)}
          />

          <MetricCard
            title="OI CHG PCR"
            value={formatNumber(market.oiChgPCR, 2)}
          />
        </View>

        <View style={styles.row}>
          <MetricCard
            title="CE WALL"
            value={formatNumber(market.ceWall, 0)}
          />

          <MetricCard
            title="PE WALL"
            value={formatNumber(market.peWall, 0)}
          />
        </View>

        <View style={styles.card}>
          <Text style={styles.label}>VOLATILITY</Text>

          <View
            style={{
              flexDirection: 'row',
              justifyContent: 'space-between',
              marginTop: 10,
              gap: 12,
            }}
          >
            <SmallText
              label="ATM IV"
              value={formatNumber(market.atmIV, 2)}
            />

            <SmallText
              label="IV RANK"
              value={formatNumber(market.ivRank, 1)}
            />

            <SmallText
              label="HV30"
              value={formatNumber(market.hv30, 2)}
            />

            <SmallText
              label="VIX REGIME"
              value={textValue(market.vixRegime) || '—'}
            />
          </View>
        </View>

        <View style={styles.card}>
          <Text style={styles.label}>IMPORTANT ALERTS</Text>

          <AlertRow
            label="Trap"
            value={formatObject(market.trapWarn)}
          />

          <AlertRow
            label="Smart Money"
            value={formatObject(market.smartMoneySummary)}
          />

          <AlertRow
            label="Futures Signal"
            value={formatObject(market.futSignal)}
          />

          <AlertRow
            label="Capital Confirmation"
            value={formatObject(market.capitalConfirmation)}
          />

          {Array.isArray(market.signals) && market.signals.length > 0 ? (
            <Text
              style={{
                color: '#9ba5b4',
                marginTop: 10,
                fontSize: 12,
              }}
            >
              {market.signals.length} active signal
              {market.signals.length === 1 ? '' : 's'}
            </Text>
          ) : null}
        </View>

        <Text
          style={{
            color: '#58616d',
            textAlign: 'center',
            fontSize: 11,
            marginTop: 4,
          }}
        >
          {market.dataSource
            ? `Source: ${market.dataSource}`
            : 'mTerminals'}
          {market.lastUpdated
            ? `  •  ${market.lastUpdated}`
            : ''}
        </Text>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = {
  row: {
    flexDirection: 'row' as const,
    gap: 12,
  },

  card: {
    backgroundColor: '#15191f',
    borderRadius: 16,
    padding: 16,
  },

  decisionCard: {
    backgroundColor: '#15191f',
    borderRadius: 18,
    padding: 18,
  },

  label: {
    color: '#7f8a99',
    fontSize: 11,
    fontWeight: '700' as const,
    letterSpacing: 0.8,
  },
};

function MetricCard({
  title,
  value,
  secondary,
  direction,
}: {
  title: string;
  value: string;
  secondary?: string;
  direction?: 'up' | 'down' | 'flat';
}) {
  return (
    <View
      style={{
        flex: 1,
        minHeight: 104,
        backgroundColor: '#15191f',
        borderRadius: 16,
        padding: 15,
      }}
    >
      <Text style={styles.label}>{title}</Text>

      <Text
        style={{
          color: '#ffffff',
          fontSize: 20,
          fontWeight: '800',
          marginTop: 8,
        }}
      >
        {value}
      </Text>

      {secondary ? (
        <Text
          style={{
            color:
              direction === 'up'
                ? '#63d297'
                : direction === 'down'
                  ? '#ef7777'
                  : '#8c96a5',
            fontSize: 12,
            marginTop: 5,
          }}
        >
          {secondary}
        </Text>
      ) : null}
    </View>
  );
}

function SmallText({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <View style={{ flex: 1 }}>
      <Text style={styles.label}>{label}</Text>

      <Text
        style={{
          color: '#ffffff',
          fontSize: 13,
          fontWeight: '700',
          marginTop: 4,
        }}
      >
        {value}
      </Text>
    </View>
  );
}

function AlertRow({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  if (!value || value === '—') return null;

  return (
    <View
      style={{
        marginTop: 10,
        paddingTop: 10,
        borderTopWidth: 1,
        borderTopColor: '#232830',
      }}
    >
      <Text
        style={{
          color: '#ffffff',
          fontSize: 13,
          fontWeight: '700',
        }}
      >
        {label}
      </Text>

      <Text
        style={{
          color: '#9ba5b4',
          fontSize: 12,
          marginTop: 3,
        }}
        numberOfLines={3}
      >
        {value}
      </Text>
    </View>
  );
}

function readDecision(value: any) {
  if (!value) {
    return {
      action: '',
      bias: '',
      reason: '',
    };
  }

  if (typeof value === 'string') {
    return {
      action: value,
      bias: '',
      reason: '',
    };
  }

  return {
    action:
      textValue(value.action) ||
      textValue(value.decision) ||
      textValue(value.signal) ||
      textValue(value.status) ||
      textValue(value.trade) ||
      '',

    bias:
      textValue(value.bias) ||
      textValue(value.direction) ||
      '',

    reason:
      textValue(value.reason) ||
      textValue(value.explanation) ||
      textValue(value.summary) ||
      '',
  };
}

function readConfidence(value: any): number | null {
  if (!value || typeof value !== 'object') return null;

  const candidate =
    value.confidence ??
    value.confidencePct ??
    value.confidence_pct ??
    value.score ??
    null;

  const number = Number(candidate);

  if (!Number.isFinite(number)) return null;

  if (number >= 0 && number <= 1) {
    return number * 100;
  }

  return number;
}

function textValue(value: any): string {
  if (value === null || value === undefined) return '';

  if (
    typeof value === 'string' ||
    typeof value === 'number' ||
    typeof value === 'boolean'
  ) {
    return String(value);
  }

  return '';
}

function formatNumber(
  value: any,
  decimals = 2,
): string {
  const number = Number(value);

  if (!Number.isFinite(number)) return '—';

  return number.toLocaleString('en-IN', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
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

function changeText(
  change: any,
  percent: any,
): string {
  const c = Number(change);
  const p = Number(percent);

  const left = Number.isFinite(c)
    ? `${c >= 0 ? '+' : ''}${c.toFixed(2)}`
    : '';

  const right = Number.isFinite(p)
    ? `${p >= 0 ? '+' : ''}${p.toFixed(2)}%`
    : '';

  return [left, right].filter(Boolean).join('  ');
}

function directionFromNumber(
  value: any,
): 'up' | 'down' | 'flat' {
  const number = Number(value);

  if (!Number.isFinite(number) || number === 0) {
    return 'flat';
  }

  return number > 0 ? 'up' : 'down';
}

function decisionColor(value: any): string {
  const text = String(value || '').toUpperCase();

  if (
    text.includes('BUY') ||
    text.includes('BULL') ||
    text.includes('LONG')
  ) {
    return '#63d297';
  }

  if (
    text.includes('SELL') ||
    text.includes('BEAR') ||
    text.includes('SHORT')
  ) {
    return '#ef7777';
  }

  return '#f2c96d';
}

function formatObject(value: any): string {
  if (value === null || value === undefined) return '—';

  if (
    typeof value === 'string' ||
    typeof value === 'number' ||
    typeof value === 'boolean'
  ) {
    return String(value);
  }

  if (Array.isArray(value)) {
    if (!value.length) return '—';

    return value
      .slice(0, 3)
      .map(formatObject)
      .filter(v => v !== '—')
      .join(' • ');
  }

  if (typeof value === 'object') {
    const preferred = [
      'summary',
      'message',
      'reason',
      'signal',
      'status',
      'bias',
      'label',
      'value',
    ];

    const values = preferred
      .map(key => value[key])
      .filter(
        item =>
          item !== null &&
          item !== undefined &&
          typeof item !== 'object',
      )
      .map(String);

    if (values.length) {
      return values.slice(0, 3).join(' • ');
    }
  }

  return 'Available';
}
