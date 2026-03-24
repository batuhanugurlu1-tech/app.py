import React, { useState, useEffect, useMemo } from 'react';
import { initializeApp } from 'firebase/app';
import { getAuth, signInAnonymously, onAuthStateChanged, signInWithCustomToken } from 'firebase/auth';
import { getFirestore, collection, doc, setDoc, addDoc, onSnapshot, query, limit, orderBy } from 'firebase/firestore';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, AreaChart, Area } from 'recharts';
import { Shield, TrendingUp, Activity, History, Settings, RefreshCcw, Zap, Target, Lock } from 'lucide-react';

// --- CONFIGURATION & FIREBASE SETUP ---
const firebaseConfig = JSON.parse(window.__firebase_config || '{}');
const appId = typeof window.__app_id !== 'undefined' ? window.__app_id : 'quant-lab-v7';
const app = initializeApp(firebaseConfig);
const auth = getAuth(app);
const db = getFirestore(app);

// --- TECHNICAL ANALYSIS UTILS ---
const calculateEMA = (data, period) => {
  const k = 2 / (period + 1);
  let ema = [data[0]];
  for (let i = 1; i < data.length; i++) {
    ema.push(data[i] * k + ema[i - 1] * (1 - k));
  }
  return ema;
};

export default function App() {
  const [user, setUser] = useState(null);
  const [symbol, setSymbol] = useState('BTCUSDT');
  const [price, setPrice] = useState(0);
  const [history, setHistory] = useState([]);
  const [currentPos, setCurrentPos] = useState(null);
  const [isBotRunning, setIsBotRunning] = useState(true);
  const [lastCheck, setLastCheck] = useState(null);
  const [settings] = useState({ emaF: 9, emaS: 21, adxT: 15, tp: 4.5 });

  // 1. Auth & Initial Load
  useEffect(() => {
    const initAuth = async () => {
      if (typeof window.__initial_auth_token !== 'undefined' && window.__initial_auth_token) {
        await signInWithCustomToken(auth, window.__initial_auth_token);
      } else {
        await signInAnonymously(auth);
      }
    };
    initAuth();
    const unsubscribe = onAuthStateChanged(auth, (u) => setUser(u));
    return () => unsubscribe();
  }, []);

  // 2. Real-time Firestore Sync (Trades & Position)
  useEffect(() => {
    if (!user) return;

    // Trade History Listener
    const tradesRef = collection(db, 'artifacts', appId, 'users', user.uid, 'trade_history');
    const q = query(tradesRef, orderBy('timestamp', 'desc'), limit(20));
    const unsubscribeHistory = onSnapshot(q, (snapshot) => {
      const data = snapshot.docs.map(doc => ({ id: doc.id, ...doc.data() }));
      setHistory(data);
    }, (err) => console.error("History Error:", err));

    // Active Position Listener
    const activePosRef = doc(db, 'artifacts', appId, 'users', user.uid, 'active_position', 'current');
    const unsubscribeActive = onSnapshot(activePosRef, (snapshot) => {
      if (snapshot.exists()) {
        const data = snapshot.data();
        setCurrentPos(Object.keys(data).length > 0 ? data : null);
      } else {
        setCurrentPos(null);
      }
    }, (err) => console.error("Active Pos Error:", err));

    return () => {
      unsubscribeHistory();
      unsubscribeActive();
    };
  }, [user]);

  // 3. Main Bot Loop (Every 10 seconds)
  useEffect(() => {
    const mainLoop = async () => {
      try {
        // Fetch Current Price
        const priceRes = await fetch(`https://api.binance.com/api/v3/ticker/price?symbol=${symbol}`);
        const priceData = await priceRes.json();
        const currentPrice = parseFloat(priceData.price);
        setPrice(currentPrice);
        setLastCheck(new Date().toLocaleTimeString());

        if (isBotRunning && user) {
          await runTradingLogic(currentPrice);
        }
      } catch (e) {
        console.error("Bot Loop Error:", e);
      }
    };

    const interval = setInterval(mainLoop, 10000);
    return () => clearInterval(interval);
  }, [symbol, currentPos, isBotRunning, user]);

  const runTradingLogic = async (p) => {
    // A. POZİSYON KAPATMA KONTROLÜ
    if (currentPos) {
      let exit = false;
      let result = "";
      
      if (currentPos.type === 'LONG') {
        if (p <= currentPos.sl) { exit = true; result = "LOSS"; }
        else if (p >= currentPos.tp) { exit = true; result = "WIN"; }
      } else {
        if (p >= currentPos.sl) { exit = true; result = "LOSS"; }
        else if (p <= currentPos.tp) { exit = true; result = "WIN"; }
      }

      if (exit) {
        const pnl = currentPos.type === 'LONG' 
          ? ((p - currentPos.entry) / currentPos.entry) * 100 
          : ((currentPos.entry - p) / currentPos.entry) * 100;

        await addDoc(collection(db, 'artifacts', appId, 'users', user.uid, 'trade_history'), {
          symbol, type: currentPos.type, entry: currentPos.entry, exit: p,
          pnl: pnl.toFixed(2), result, timestamp: Date.now()
        });
        await setDoc(doc(db, 'artifacts', appId, 'users', user.uid, 'active_position', 'current'), {});
      }
      return;
    }

    // B. YENİ POZİSYON AÇMA (Indicator Analysis)
    // 15m candle verisi çekilir
    const klinesRes = await fetch(`https://api.binance.com/api/v3/klines?symbol=${symbol}&interval=15m&limit=100`);
    const klines = await klinesRes.json();
    const closes = klines.map(k => parseFloat(k[4]));
    const highs = klines.map(k => parseFloat(k[2]));
    const lows = klines.map(k => parseFloat(k[3]));

    const emaF = calculateEMA(closes, settings.emaF);
    const emaS = calculateEMA(closes, settings.emaS);
    
    const lastF = emaF[emaF.length - 1];
    const lastS = emaS[emaS.length - 1];
    const prevF = emaF[emaF.length - 2];
    const prevS = emaS[emaS.length - 2];

    // ATR Hesabı (Basitleştirilmiş)
    const atr = (highs[highs.length-1] - lows[lows.length-1]);

    // Trend ve Kesişim Kontrolü
    if (prevF <= prevS && lastF > lastS) { // LONG Cross
      const sl = p - (atr * 2);
      const tp = p + (atr * settings.tp);
      await setDoc(doc(db, 'artifacts', appId, 'users', user.uid, 'active_position', 'current'), {
        type: 'LONG', entry: p, sl, tp, timestamp: Date.now(), symbol
      });
    } else if (prevF >= prevS && lastF < lastS) { // SHORT Cross
      const sl = p + (atr * 2);
      const tp = p - (atr * settings.tp);
      await setDoc(doc(db, 'artifacts', appId, 'users', user.uid, 'active_position', 'current'), {
        type: 'SHORT', entry: p, sl, tp, timestamp: Date.now(), symbol
      });
    }
  };

  const winRate = useMemo(() => {
    if (history.length === 0) return 0;
    const wins = history.filter(h => h.result === 'WIN').length;
    return (wins / history.length) * 100;
  }, [history]);

  const totalPnL = useMemo(() => {
    return history.reduce((acc, curr) => acc + parseFloat(curr.pnl), 0).toFixed(2);
  }, [history]);

  return (
    <div className="min-h-screen bg-[#0E1117] text-white p-4 font-sans selection:bg-[#00FF41]/30">
      {/* TOP BAR */}
      <div className="max-w-7xl mx-auto flex flex-col md:flex-row justify-between items-center mb-8 gap-4 bg-[#1A1D23] p-6 rounded-3xl border border-gray-800 shadow-2xl">
        <div className="flex items-center gap-4">
          <div className="bg-[#00FF41]/10 p-3 rounded-2xl">
            <Shield size={32} className="text-[#00FF41]" />
          </div>
          <div>
            <h1 className="text-2xl font-black tracking-tighter text-white">CLOUD SENTINEL <span className="text-[#00FF41]">V7.0</span></h1>
            <div className="flex items-center gap-2 text-xs text-gray-500">
              <div className="w-2 h-2 rounded-full bg-[#00FF41] animate-pulse"></div>
              OTOPİLOT AKTİF • SON GÜNCELLEME: {lastCheck}
            </div>
          </div>
        </div>

        <div className="flex gap-4">
          <div className="text-right">
            <div className="text-[10px] text-gray-500 uppercase font-bold tracking-widest">Canlı {symbol}</div>
            <div className="text-2xl font-mono font-black text-[#00FF41]">{price.toLocaleString()} $</div>
          </div>
          <button 
            onClick={() => setIsBotRunning(!isBotRunning)}
            className={`px-6 py-2 rounded-2xl font-bold transition-all flex items-center gap-2 ${isBotRunning ? 'bg-red-500/10 text-red-500 hover:bg-red-500/20' : 'bg-[#00FF41]/10 text-[#00FF41] hover:bg-[#00FF41]/20'}`}
          >
            {isBotRunning ? <><Lock size={18}/> BOTU DURDUR</> : <><Zap size={18}/> BOTU BAŞLAT</>}
          </button>
        </div>
      </div>

      <div className="max-w-7xl mx-auto grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* LEFT COLUMN: ACTIVE STATUS */}
        <div className="lg:col-span-2 space-y-8">
          
          {/* STATS OVERVIEW */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="bg-[#1A1D23] p-6 rounded-3xl border border-gray-800">
              <div className="text-gray-500 text-xs font-bold uppercase mb-1">Win Rate</div>
              <div className="text-3xl font-black text-[#00FF41]">{winRate.toFixed(1)}%</div>
            </div>
            <div className="bg-[#1A1D23] p-6 rounded-3xl border border-gray-800">
              <div className="text-gray-500 text-xs font-bold uppercase mb-1">Net Kâr/Zarar</div>
              <div className={`text-3xl font-black ${parseFloat(totalPnL) >= 0 ? 'text-[#00FF41]' : 'text-red-500'}`}>%{totalPnL}</div>
            </div>
            <div className="bg-[#1A1D23] p-6 rounded-3xl border border-gray-800">
              <div className="text-gray-500 text-xs font-bold uppercase mb-1">Toplam İşlem</div>
              <div className="text-3xl font-black text-white">{history.length}</div>
            </div>
          </div>

          {/* ACTIVE POSITION MONITOR */}
          <div className="bg-[#1A1D23] rounded-3xl border-2 border-[#00FF41]/20 p-8 relative overflow-hidden shadow-2xl shadow-[#00FF41]/5">
            <div className="flex justify-between items-center mb-8">
              <h2 className="text-xl font-black flex items-center gap-3">
                <Activity className="text-[#00FF41]" /> CANLI POZİSYON TAKİBİ
              </h2>
              {!currentPos && (
                <div className="flex items-center gap-2 text-xs font-bold text-gray-500 bg-gray-800/50 px-4 py-2 rounded-full">
                  <RefreshCcw size={14} className="animate-spin" /> PİYASA TARANIYOR...
                </div>
              )}
            </div>

            {currentPos ? (
              <div className="grid grid-cols-2 md:grid-cols-4 gap-8">
                <div className="space-y-1">
                  <div className="text-xs text-gray-500 font-bold uppercase">Yön</div>
                  <div className={`text-2xl font-black ${currentPos.type === 'LONG' ? 'text-[#00FF41]' : 'text-red-500'}`}>{currentPos.type}</div>
                </div>
                <div className="space-y-1">
                  <div className="text-xs text-gray-500 font-bold uppercase">Giriş Fiyatı</div>
                  <div className="text-2xl font-mono font-bold text-white">{currentPos.entry.toLocaleString()}</div>
                </div>
                <div className="space-y-1">
                  <div className="text-xs text-gray-500 font-bold uppercase">Hedef (TP)</div>
                  <div className="text-2xl font-mono font-bold text-[#00FF41]">{currentPos.tp.toLocaleString()}</div>
                </div>
                <div className="space-y-1">
                  <div className="text-xs text-gray-500 font-bold uppercase">Anlık Kar/Zarar</div>
                  <div className={`text-3xl font-black ${((price - currentPos.entry) * (currentPos.type === 'LONG' ? 1 : -1)) >= 0 ? 'text-[#00FF41]' : 'text-red-500'}`}>
                    %{currentPos.type === 'LONG' 
                      ? (((price - currentPos.entry)/currentPos.entry)*100).toFixed(2)
                      : (((currentPos.entry - price)/currentPos.entry)*100).toFixed(2)}
                  </div>
                </div>
              </div>
            ) : (
              <div className="py-12 text-center text-gray-600">
                <p className="text-lg font-bold mb-2">Şu an açık pozisyon yok.</p>
                <p className="text-sm opacity-50 italic">Bot, EMA 9/21 kesişimi ve ATR onayı bekliyor...</p>
              </div>
            )}
            
            <div className="absolute -bottom-12 -right-12 w-48 h-48 bg-[#00FF41]/5 rounded-full blur-3xl"></div>
          </div>

          {/* HISTORY TABLE */}
          <div className="bg-[#1A1D23] rounded-3xl border border-gray-800 overflow-hidden shadow-xl">
            <div className="p-6 border-b border-gray-800 flex justify-between items-center bg-[#1A1D23]">
              <h2 className="font-black flex items-center gap-3"><History /> BULUT İŞLEM GEÇMİŞİ</h2>
              <span className="text-[10px] bg-gray-800 px-3 py-1 rounded-full text-gray-400 font-bold uppercase tracking-widest">Senkronize</span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left">
                <thead className="bg-[#0E1117]/50 text-gray-500 text-[10px] font-black uppercase tracking-widest">
                  <tr>
                    <th className="p-6">Zaman</th>
                    <th className="p-6">Varlık</th>
                    <th className="p-6">Tip</th>
                    <th className="p-6">P&L (%)</th>
                    <th className="p-6">Sonuç</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-800/50">
                  {history.map((t) => (
                    <tr key={t.id} className="hover:bg-white/[0.02] transition-colors group">
                      <td className="p-6 text-sm text-gray-400 font-mono">{new Date(t.timestamp).toLocaleTimeString()}</td>
                      <td className="p-6 text-sm font-bold text-white">{t.symbol}</td>
                      <td className={`p-6 text-sm font-black ${t.type === 'LONG' ? 'text-[#00FF41]' : 'text-red-500'}`}>{t.type}</td>
                      <td className={`p-6 text-lg font-mono font-black ${parseFloat(t.pnl) >= 0 ? 'text-[#00FF41]' : 'text-red-500'}`}>%{t.pnl}</td>
                      <td className="p-6">
                        <div className={`inline-flex px-3 py-1 rounded-lg text-[10px] font-black uppercase ${t.result === 'WIN' ? 'bg-[#00FF41]/10 text-[#00FF41]' : 'bg-red-500/10 text-red-500'}`}>
                          {t.result}
                        </div>
                      </td>
                    </tr>
                  ))}
                  {history.length === 0 && (
                    <tr><td colSpan="5" className="p-20 text-center text-gray-600 font-bold italic">İşlem geçmişi bulunamadı.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        {/* RIGHT COLUMN: CONFIGURATION */}
        <div className="space-y-8">
          <div className="bg-[#1A1D23] rounded-3xl border border-gray-800 p-8 shadow-xl">
            <h2 className="font-black mb-6 flex items-center gap-3"><Settings className="text-[#00FF41]" /> BOT KONTROL</h2>
            
            <div className="space-y-6">
              <div className="space-y-2">
                <label className="text-[10px] text-gray-500 font-black uppercase tracking-widest">Aktif Varlık</label>
                <select 
                  className="w-full bg-[#0E1117] border border-gray-800 rounded-2xl p-4 text-sm font-bold focus:ring-2 focus:ring-[#00FF41] outline-none transition-all"
                  value={symbol}
                  onChange={(e) => setSymbol(e.target.value)}
                >
                  <option value="BTCUSDT">BITCOIN (BTC)</option>
                  <option value="ETHUSDT">ETHEREUM (ETH)</option>
                  <option value="SOLUSDT">SOLANA (SOL)</option>
                </select>
              </div>

              <div className="p-6 bg-[#0E1117] rounded-2xl border border-gray-800">
                <h3 className="text-xs font-black text-gray-500 uppercase mb-4 tracking-widest flex items-center gap-2">
                  <Target size={14} className="text-[#00FF41]" /> Strateji Motoru
                </h3>
                <div className="space-y-3">
                  <div className="flex justify-between text-xs">
                    <span className="text-gray-400">Trend Takibi</span>
                    <span className="text-[#00FF41] font-bold">EMA 9/21</span>
                  </div>
                  <div className="flex justify-between text-xs">
                    <span className="text-gray-400">Risk Yönetimi</span>
                    <span className="text-[#00FF41] font-bold">ATR x2 (Stop)</span>
                  </div>
                  <div className="flex justify-between text-xs">
                    <span className="text-gray-400">Kâr Hedefi</span>
                    <span className="text-[#00FF41] font-bold">ATR x4.5 (TP)</span>
                  </div>
                </div>
              </div>

              <div className="text-[10px] text-gray-500 text-center italic leading-relaxed">
                Bu uygulama senkronize çalışır. Tarayıcıyı kapatsanız dahi, Railway üzerindeki sunucunuz aktif pozisyonları takip etmeye devam edebilir.
              </div>
            </div>
          </div>

          <div className="bg-gradient-to-br from-[#00FF41]/10 to-transparent rounded-3xl border border-[#00FF41]/20 p-8">
             <h2 className="font-black mb-4 flex items-center gap-3 text-[#00FF41]"><Zap size={20} /> EKİP NOTU</h2>
             <p className="text-xs text-gray-400 leading-relaxed">
                Şu an "Sanal Portföy" modundasın. Bot, bulut veritabanını kullanarak işlemlerini gerçek fiyatlarla simüle ediyor. Başarı oranını (Win Rate) buradan canlı izleyebilirsin.
             </p>
          </div>
        </div>
      </div>
    </div>
  );
}
