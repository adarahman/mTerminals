import { ScrollView, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { MarketContextBar } from '../../src/components/MarketContextBar';
import { useMarketData } from '../../src/hooks/useMarketData';

export default function AnalyticsScreen() {
  const { payload, connected, error } = useMarketData();
  const m: any = payload?.market ?? {};

  const ceFlow = number(m.totalCeCapitalFlow);
  const peFlow = number(m.totalPeCapitalFlow);
  const netFlow = number(m.netCapitalFlow);

  return (
    <SafeAreaView
      style={{ flex: 1, backgroundColor: '#0b0d10' }}
      edges={['top']}
    >
      <MarketContextBar />

      <ScrollView
        contentContainerStyle={{
          padding: 14,
          paddingBottom: 50,
          gap: 12,
        }}
      >
        {/* HEADER */}
        <View
          style={{
            flexDirection: 'row',
            justifyContent: 'space-between',
            alignItems: 'flex-start',
          }}
        >
          <View>
            <Text style={styles.pageTitle}>
              Analytics
            </Text>

            <Text style={styles.subtitle}>
              {m.symbol || '—'}
              {'  •  '}
              {m.expiry || '—'}
              {'  •  ATM '}
              {m.atm ?? '—'}
            </Text>
          </View>

          <Text
            style={{
              color: connected
                ? '#63d297'
                : '#ef7777',
              fontSize: 12,
              fontWeight: '800',
            }}
          >
            {connected ? '● LIVE' : '● OFFLINE'}
          </Text>
        </View>

        {error ? (
          <Card>
            <Text style={{ color: '#ef7777' }}>
              {error}
            </Text>
          </Card>
        ) : null}

        {/* CAPITAL FLOW */}
        <Card>
          <SectionTitle>CAPITAL FLOW</SectionTitle>

          <Text
            style={{
              color: flowColor(netFlow),
              fontSize: 26,
              fontWeight: '900',
              marginTop: 8,
            }}
          >
            {signedMoney(netFlow)}
          </Text>

          <Text style={styles.muted}>
            Net options capital flow
          </Text>

          <View style={styles.grid}>
            <Metric
              label="CE FLOW"
              value={signedMoney(ceFlow)}
              tone={flowTone(ceFlow)}
            />

            <Metric
              label="PE FLOW"
              value={signedMoney(peFlow)}
              tone={flowTone(peFlow)}
            />

            <Metric
              label="CAPITAL PCR"
              value={format(m.capitalPCR, 2)}
            />

            <Metric
              label="CONCENTRATION"
              value={formatFlexible(
                m.capitalConcentration,
              )}
            />
          </View>

          <FlowBalance
            ce={Math.abs(ceFlow || 0)}
            pe={Math.abs(peFlow || 0)}
          />

          <Insight
            label="Confirmation"
            value={objectText(
              m.capitalConfirmation,
            )}
          />

          <Insight
            label="Capital vs Futures"
            value={objectText(
              m.capitalVsFutures,
            )}
          />
        </Card>

        {/* SMART MONEY */}
        <Card>
          <SectionTitle>SMART MONEY</SectionTitle>

          <Text
            style={{
              color: '#ffffff',
              fontSize: 17,
              fontWeight: '800',
              marginTop: 9,
              lineHeight: 23,
            }}
          >
            {objectText(m.smartMoneySummary)}
          </Text>

          <Insight
            label="FII / DII Bias"
            value={objectText(m.fiiDiiBias)}
          />

          <Insight
            label="FII / DII Sentiment"
            value={objectText(
              m.fiiDiiSentiment,
            )}
          />

          <Insight
            label="Futures / Options"
            value={objectText(
              m.futuresOptionsDivergence,
            )}
          />

          <Insight
            label="Futures Signal"
            value={objectText(m.futSignal)}
          />
        </Card>

        {/* CAPITAL WALLS */}
        <Card>
          <SectionTitle>
            CAPITAL STRUCTURE
          </SectionTitle>

          <View style={styles.grid}>
            <Metric
              label="CE CAPITAL WALL"
              value={format(
                m.capitalCeWallStrike,
                0,
              )}
            />

            <Metric
              label="PE CAPITAL WALL"
              value={format(
                m.capitalPeWallStrike,
                0,
              )}
            />

            <Metric
              label="PREMIUM LOCKED"
              value={money(
                m.totalPremiumLockedCapital ??
                  m.netPremiumLocked,
              )}
            />

            <Metric
              label="PREMIUM TURNOVER"
              value={money(
                m.totalPremiumTurnoverCapital,
              )}
            />

            <Metric
              label="NOTIONAL"
              value={money(
                m.totalNotionalExposureCapital,
              )}
            />

            <Metric
              label="MAX PAIN"
              value={format(m.maxPain, 0)}
            />
          </View>
        </Card>

        {/* EXPOSURE */}
        <Card>
          <SectionTitle>
            POSITIONING EXPOSURE
          </SectionTitle>

          <View style={styles.grid}>
            <Metric
              label="NET DELTA"
              value={signedMoney(
                m.netDeltaExposureCapital,
              )}
              tone={flowTone(
                number(
                  m.netDeltaExposureCapital,
                ),
              )}
            />

            <Metric
              label="NET GAMMA"
              value={signedMoney(
                m.netGammaExposureCapital,
              )}
              tone={flowTone(
                number(
                  m.netGammaExposureCapital,
                ),
              )}
            />

            <Metric
              label="CE WALL"
              value={format(m.ceWall, 0)}
            />

            <Metric
              label="PE WALL"
              value={format(m.peWall, 0)}
            />
          </View>
        </Card>

        {/* VOLATILITY */}
        <Card>
          <SectionTitle>
            VOLATILITY & IV
          </SectionTitle>

          <View style={styles.grid}>
            <Metric
              label="ATM IV"
              value={format(m.atmIV, 2)}
            />

            <Metric
              label="IV RANK"
              value={format(m.ivRank, 1)}
            />

            <Metric
              label="HV30"
              value={format(m.hv30, 2)}
            />

            <Metric
              label="INDIA VIX"
              value={format(m.indiaVix, 2)}
              sub={percent(
                m.indiaVixChgPct,
              )}
              tone={changeTone(
                m.indiaVixChgPct,
              )}
            />

            <Metric
              label="CE ATM IV"
              value={format(m.atmCeIV, 2)}
            />

            <Metric
              label="PE ATM IV"
              value={format(m.atmPeIV, 2)}
            />

            <Metric
              label="ATM SKEW"
              value={formatFlexible(
                m.atmSkew,
              )}
            />

            <Metric
              label="VIX REGIME"
              value={objectText(m.vixRegime)}
            />
          </View>
        </Card>

        {/* GREEKS */}
        <Card>
          <SectionTitle>ATM GREEKS</SectionTitle>

          <View style={styles.greeks}>
            <Greek
              symbol="Δ"
              label="DELTA"
              value={m.atmDelta}
            />

            <Greek
              symbol="Γ"
              label="GAMMA"
              value={m.atmGamma}
            />

            <Greek
              symbol="Θ"
              label="THETA"
              value={m.atmTheta}
            />

            <Greek
              symbol="ν"
              label="VEGA"
              value={m.atmVega}
            />
          </View>
        </Card>

        {/* OI VELOCITY */}
        <Card>
          <SectionTitle>
            OI VELOCITY
          </SectionTitle>

          <Text style={styles.muted}>
            Strongest ΔOI activity near ATM
          </Text>

          <View
            style={{
              marginTop: 10,
              gap: 10,
            }}
          >
            {Array.isArray(m.oiVelocity) &&
            m.oiVelocity.length ? (
              m.oiVelocity.map(
                (window: any) => (
                  <VelocitySummary
                    key={String(
                      window.window,
                    )}
                    data={window}
                    atm={number(m.atm)}
                  />
                ),
              )
            ) : (
              <Text style={styles.muted}>
                No OI velocity data.
              </Text>
            )}
          </View>
        </Card>

        {/* MARKET INTERPRETATION */}
        <Card>
          <SectionTitle>
            MARKET INTERPRETATION
          </SectionTitle>

          <Insight
            label="Composite Bias"
            value={objectText(
              m.compositeBias,
            )}
          />

          <Insight
            label="Spot Bias"
            value={objectText(m.spotBias)}
          />

          <Insight
            label="Market Regime"
            value={objectText(
              m.marketRegime,
            )}
          />

          <Insight
            label="PCR Sentiment"
            value={objectText(
              m.pcrSentiment,
            )}
          />

          <Insight
            label="Trap Warning"
            value={objectText(m.trapWarn)}
          />
        </Card>

        <Text style={styles.footer}>
          {m.dataSource
            ? `Source: ${m.dataSource}`
            : 'mTerminals'}
          {m.lastUpdated
            ? `  •  ${m.lastUpdated}`
            : ''}
        </Text>
      </ScrollView>
    </SafeAreaView>
  );
}

/* ---------------- COMPONENTS ---------------- */

function VelocitySummary({
  data,
  atm,
}: {
  data: any;
  atm: number | null;
}) {
  const rows = Array.isArray(data?.rows)
    ? data.rows
    : [];

  const active = rows.filter(
    (r: any) =>
      number(r.ceDOI) !== 0 ||
      number(r.peDOI) !== 0,
  );

  const source =
    active.length > 0 ? active : rows;

  const strongest = [...source]
    .sort(
      (a, b) =>
        strength(b) - strength(a),
    )
    .slice(0, 5);

  return (
    <View
      style={{
        backgroundColor: '#101419',
        borderRadius: 12,
        padding: 12,
      }}
    >
      <View
        style={{
          flexDirection: 'row',
          justifyContent: 'space-between',
        }}
      >
        <Text
          style={{
            color: '#ffffff',
            fontSize: 15,
            fontWeight: '800',
          }}
        >
          {data.window}m
        </Text>

        <Text
          style={{
            color: active.length
              ? '#63d297'
              : '#7f8a99',
            fontSize: 10,
            fontWeight: '700',
          }}
        >
          {active.length
            ? `${active.length} ACTIVE`
            : 'NO ΔOI YET'}
        </Text>
      </View>

      {strongest.map((row: any) => {
        const strike = number(row.strike);

        return (
          <View
            key={String(row.strike)}
            style={{
              flexDirection: 'row',
              paddingVertical: 7,
              borderTopWidth: 1,
              borderTopColor: '#232830',
              marginTop: 5,
            }}
          >
            <Text
              style={{
                width: 58,
                color:
                  strike === atm
                    ? '#ffffff'
                    : '#9ba5b4',
                fontWeight:
                  strike === atm
                    ? '900'
                    : '600',
              }}
            >
              {strike === atm ? '▶ ' : ''}
              {row.strike}
            </Text>

            <Text
              style={{
                flex: 1,
                color: changeColor(
                  row.ceDOI,
                ),
                fontSize: 11,
              }}
            >
              CE {signedCompact(
                row.ceDOI,
              )}
            </Text>

            <Text
              style={{
                flex: 1,
                color: changeColor(
                  row.peDOI,
                ),
                textAlign: 'right',
                fontSize: 11,
              }}
            >
              PE {signedCompact(
                row.peDOI,
              )}
            </Text>
          </View>
        );
      })}
    </View>
  );
}

function FlowBalance({
  ce,
  pe,
}: {
  ce: number;
  pe: number;
}) {
  const total = ce + pe;

  const cePct =
    total > 0 ? (ce / total) * 100 : 50;

  const pePct = 100 - cePct;

  return (
    <View style={{ marginTop: 16 }}>
      <View
        style={{
          flexDirection: 'row',
          justifyContent: 'space-between',
          marginBottom: 5,
        }}
      >
        <Text
          style={{
            color: '#ef7777',
            fontSize: 10,
            fontWeight: '700',
          }}
        >
          CE {cePct.toFixed(0)}%
        </Text>

        <Text
          style={{
            color: '#63d297',
            fontSize: 10,
            fontWeight: '700',
          }}
        >
          PE {pePct.toFixed(0)}%
        </Text>
      </View>

      <View
        style={{
          flexDirection: 'row',
          height: 7,
          borderRadius: 6,
          overflow: 'hidden',
          backgroundColor: '#242a32',
        }}
      >
        <View
          style={{
            width: `${cePct}%`,
            backgroundColor: '#ef7777',
          }}
        />

        <View
          style={{
            width: `${pePct}%`,
            backgroundColor: '#63d297',
          }}
        />
      </View>
    </View>
  );
}

function Greek({
  symbol,
  label,
  value,
}: {
  symbol: string;
  label: string;
  value: any;
}) {
  return (
    <View
      style={{
        width: '48%',
        backgroundColor: '#101419',
        borderRadius: 12,
        padding: 13,
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

      <View
        style={{
          flexDirection: 'row',
          alignItems: 'baseline',
          gap: 7,
          marginTop: 5,
        }}
      >
        <Text
          style={{
            color: '#7f8a99',
            fontSize: 17,
          }}
        >
          {symbol}
        </Text>

        <Text
          style={{
            color: '#ffffff',
            fontSize: 18,
            fontWeight: '800',
          }}
        >
          {format(value, 4)}
        </Text>
      </View>
    </View>
  );
}

function Metric({
  label,
  value,
  sub,
  tone = 'neutral',
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: 'positive' | 'negative' | 'neutral';
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
      <Text style={styles.metricLabel}>
        {label}
      </Text>

      <Text
        numberOfLines={2}
        style={{
          color:
            tone === 'positive'
              ? '#63d297'
              : tone === 'negative'
                ? '#ef7777'
                : '#ffffff',
          fontSize: 16,
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
              tone === 'positive'
                ? '#63d297'
                : tone === 'negative'
                  ? '#ef7777'
                  : '#8c96a5',
            fontSize: 10,
            marginTop: 3,
          }}
        >
          {sub}
        </Text>
      ) : null}
    </View>
  );
}

function Insight({
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
        borderTopWidth: 1,
        borderTopColor: '#232830',
        marginTop: 11,
        paddingTop: 10,
      }}
    >
      <Text style={styles.metricLabel}>
        {label.toUpperCase()}
      </Text>

      <Text
        style={{
          color: '#d7dde5',
          fontSize: 12,
          lineHeight: 18,
          marginTop: 4,
        }}
      >
        {value}
      </Text>
    </View>
  );
}

function Card({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <View
      style={{
        backgroundColor: '#15191f',
        borderRadius: 16,
        padding: 15,
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
    <Text style={styles.sectionTitle}>
      {children}
    </Text>
  );
}

/* ---------------- HELPERS ---------------- */

const styles = {
  pageTitle: {
    color: '#ffffff',
    fontSize: 25,
    fontWeight: '800' as const,
  },

  subtitle: {
    color: '#7f8a99',
    fontSize: 12,
    marginTop: 4,
  },

  sectionTitle: {
    color: '#7f8a99',
    fontSize: 10,
    fontWeight: '800' as const,
    letterSpacing: 0.8,
  },

  metricLabel: {
    color: '#7f8a99',
    fontSize: 9,
    fontWeight: '700' as const,
    letterSpacing: 0.4,
  },

  muted: {
    color: '#7f8a99',
    fontSize: 11,
    marginTop: 5,
  },

  grid: {
    flexDirection: 'row' as const,
    flexWrap: 'wrap' as const,
    justifyContent: 'space-between' as const,
    gap: 9,
    marginTop: 12,
  },

  greeks: {
    flexDirection: 'row' as const,
    flexWrap: 'wrap' as const,
    justifyContent: 'space-between' as const,
    gap: 9,
    marginTop: 12,
  },

  footer: {
    color: '#58616d',
    textAlign: 'center' as const,
    fontSize: 10,
  },
};

function number(value: any): number | null {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function format(
  value: any,
  decimals = 2,
): string {
  const n = number(value);

  if (n === null) return '—';

  return n.toLocaleString('en-IN', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

function formatFlexible(value: any): string {
  if (
    value === null ||
    value === undefined
  ) {
    return '—';
  }

  if (typeof value === 'number') {
    return format(value, 2);
  }

  if (typeof value === 'string') {
    return value;
  }

  return objectText(value);
}

function money(value: any): string {
  const n = number(value);

  if (n === null) return '—';

  const abs = Math.abs(n);

  if (abs >= 1e12)
    return `₹${(n / 1e12).toFixed(2)}T`;

  if (abs >= 1e9)
    return `₹${(n / 1e9).toFixed(2)}B`;

  if (abs >= 1e7)
    return `₹${(n / 1e7).toFixed(2)}Cr`;

  if (abs >= 1e5)
    return `₹${(n / 1e5).toFixed(2)}L`;

  if (abs >= 1e3)
    return `₹${(n / 1e3).toFixed(1)}K`;

  return `₹${n.toFixed(0)}`;
}

function signedMoney(value: any): string {
  const n = number(value);

  if (n === null) return '—';

  if (n === 0) return '₹0';

  const result = money(Math.abs(n));

  return `${n > 0 ? '+' : '-'}${result}`;
}

function compact(value: any): string {
  const n = number(value);

  if (n === null) return '—';

  const abs = Math.abs(n);

  if (abs >= 1e7)
    return `${(abs / 1e7).toFixed(2)}Cr`;

  if (abs >= 1e5)
    return `${(abs / 1e5).toFixed(1)}L`;

  if (abs >= 1e3)
    return `${(abs / 1e3).toFixed(1)}K`;

  return abs.toFixed(0);
}

function signedCompact(value: any): string {
  const n = number(value);

  if (n === null) return '—';

  if (n === 0) return '0';

  return `${n > 0 ? '+' : '-'}${compact(n)}`;
}

function percent(value: any): string {
  const n = number(value);

  if (n === null) return '—';

  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`;
}

function flowTone(
  value: number | null,
): 'positive' | 'negative' | 'neutral' {
  if (value === null || value === 0)
    return 'neutral';

  return value > 0
    ? 'positive'
    : 'negative';
}

function changeTone(
  value: any,
): 'positive' | 'negative' | 'neutral' {
  return flowTone(number(value));
}

function flowColor(
  value: number | null,
): string {
  if (value === null || value === 0)
    return '#ffffff';

  return value > 0
    ? '#63d297'
    : '#ef7777';
}

function changeColor(value: any): string {
  const n = number(value);

  if (n === null || n === 0)
    return '#8c96a5';

  return n > 0
    ? '#63d297'
    : '#ef7777';
}

function strength(row: any): number {
  return (
    Math.abs(number(row?.ceDOI) || 0) +
    Math.abs(number(row?.peDOI) || 0)
  );
}

function objectText(value: any): string {
  if (
    value === null ||
    value === undefined
  ) {
    return '—';
  }

  if (
    typeof value === 'string' ||
    typeof value === 'number' ||
    typeof value === 'boolean'
  ) {
    return String(value);
  }

  if (Array.isArray(value)) {
    const parts = value
      .slice(0, 4)
      .map(objectText)
      .filter(
        x => x && x !== '—',
      );

    return parts.length
      ? parts.join(' • ')
      : '—';
  }

  if (typeof value === 'object') {
    const preferred = [
      'summary',
      'signal',
      'status',
      'bias',
      'sentiment',
      'confirmation',
      'message',
      'reason',
      'label',
      'value',
    ];

    const parts = preferred
      .filter(
        key =>
          value[key] !== null &&
          value[key] !== undefined &&
          typeof value[key] !==
            'object',
      )
      .map(
        key => String(value[key]),
      );

    if (parts.length)
      return parts
        .slice(0, 4)
        .join(' • ');

    const simple = Object.entries(value)
      .filter(
        ([, v]) =>
          typeof v === 'string' ||
          typeof v === 'number' ||
          typeof v === 'boolean',
      )
      .slice(0, 4)
      .map(
        ([k, v]) => `${k}: ${v}`,
      );

    return simple.length
      ? simple.join(' • ')
      : 'Available';
  }

  return '—';
}
