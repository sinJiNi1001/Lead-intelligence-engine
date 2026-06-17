import { useState, useEffect, useRef } from 'react';
import { fetchCountries, fetchHistory, analyzeLead, sendLogToServer } from './api';
import toast, { Toaster } from 'react-hot-toast';

// A helper component to match your custom Searchable Dropdowns perfectly
function SearchableSelect({ placeholder, options, value, onChange }) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const ref = useRef(null);

  useEffect(() => {
    const handleClickOutside = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener('click', handleClickOutside);
    return () => document.removeEventListener('click', handleClickOutside);
  }, []);

  const filtered = options.filter(o => typeof o === 'string' ? o.toLowerCase().includes(search.toLowerCase()) : o.label.toLowerCase().includes(search.toLowerCase()));

  return (
    <div className="searchable-select" ref={ref}>
      <input type="text" placeholder={placeholder} readOnly value={value} onClick={() => setOpen(!open)} />
      <div className={`dropdown-list ${open ? 'active' : ''}`}>
        <input type="text" className="search-input" placeholder="Search…" value={search} onChange={(e) => setSearch(e.target.value)} onClick={(e) => e.stopPropagation()} />
        <div className="dropdown-options">
          {filtered.map(opt => {
            const val = typeof opt === 'string' ? opt : opt.value;
            const lbl = typeof opt === 'string' ? opt : opt.label;
            return (
              <div key={val} className="dropdown-item" onClick={() => { onChange(val); setOpen(false); setSearch(''); }}>{lbl}</div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

export default function App() {
  // State from your original JS logic
  const [activeTab, setActiveTab] = useState('dash');
  const [loading, setLoading] = useState(false);
  const [debugLog, setDebugLog] = useState("");
  const [history, setHistory] = useState([]);
  const [isHistoryLoading, setIsHistoryLoading] = useState(true);
  const [countries, setCountries] = useState(["United States", "United Kingdom", "India", "Australia", "Canada", "Germany", "France", "Other"]);
  
  const [aiResult, setAiResult] = useState(null);
  const [resultMeta, setResultMeta] = useState({ companyName: '', website: '' });

  // Your exact form inputs
  const [formData, setFormData] = useState({
    model: 'meta-llama/llama-3.3-70b-instruct',
    company_name: '', website: '', linkedin_url: '', country: '', industry: '',
    service_type: 'Web VAPT', scope_count: 1, complexity: 'Medium',
    lead_type: 'New', clarity: 'Somewhat clear', buying_stage: 'Ready',
    price_sensitivity: 'Medium', timeline: 'Soon (1 month)', competitors: false,
    web_rate: 1500, api_rate: 1000, net_rate: 800, min_value: 2500, ent_mult: 1.5
  });

  useEffect(() => {
    const init = async () => {
      try {
        const cData = await fetchCountries();
        if (cData && cData.countries) setCountries(cData.countries);
        loadHistoryData();
      } catch (e) {}
    };
    init();
  }, []);

  const loadHistoryData = async () => {
    setIsHistoryLoading(true);
    try {
      const hData = await fetchHistory();
      if (hData && hData.history) setHistory(hData.history);
    } catch (e) { 
      console.error("History Error", e); 
    } finally {
      setIsHistoryLoading(false);
    }
  };

  const handleTabSwitch = (tabId) => {
    setActiveTab(tabId);
    if (tabId === 'hist') loadHistoryData();
  };

  const handleChange = (e) => {
    const value = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
    setFormData({ ...formData, [e.target.name]: value });
  };

  const appendDebug = (msg, level = 'info') => {
    setDebugLog(prev => prev + `\n${msg}`);
    sendLogToServer(level, msg); // Streams the log to the backend file
  };

  const handleAnalyze = async () => {
    if (!formData.company_name) { alert("Company Name is required."); return; }
    if (!formData.website) { alert("Website is required."); return; }

    setLoading(true);
    setActiveTab('dash');
    setAiResult(null);
    setResultMeta({ companyName: formData.company_name, website: formData.website });
    
    // Log the start of the request
    appendDebug(`📡 Sending payload for: ${formData.company_name} | Target: ${formData.website}`, 'info');

    try {
      const response = await analyzeLead(formData);
      appendDebug(`📥 HTTP 200 Received`, 'info');
      
      if (!response.success) {
        appendDebug(`💥 Server Error: ${response.error || 'Unknown error'}`, 'error');
        setAiResult({ _error: response.error || 'Unknown error' });
      } else {
        appendDebug(`✅ Intelligence payload parsed successfully.`, 'info');
        setAiResult(response.data);
      }
    } catch (err) {
      appendDebug(`💥 CRITICAL UI EXCEPTION:\n${err.name}: ${err.message}`, 'error');
      setAiResult({ _error: 'Request failed — see Debug Console.' });
    } finally {
      setLoading(false);
    }
  };

  // Render Helpers matching your JS template strings exactly
  const scoreColor = (s) => { const u = (s || '').toUpperCase(); return u.startsWith('HIGH') ? '#10b981' : u.startsWith('MEDIUM') ? '#f59e0b' : '#ef4444'; };
  const scoreBg = (s) => { const u = (s || '').toUpperCase(); return u.startsWith('HIGH') ? '#f0fdf4' : u.startsWith('MEDIUM') ? '#fffbeb' : '#fef2f2'; };
  const scoreBorder = (s) => { const u = (s || '').toUpperCase(); return u.startsWith('HIGH') ? '#bbf7d0' : u.startsWith('MEDIUM') ? '#fde68a' : '#fecaca'; };
  const boolLabel = (v) => v === true ? '✅ Yes' : v === false ? '❌ No' : '—';
  const unkStyle = (v) => (v === 'Unknown' || v === null || v === undefined) ? { color: '#94a3b8' } : { color: '#1e293b', fontWeight: '600' };

  // Reusable Radio button
  const RadioBtn = ({ name, value, label }) => (
    <>
      <input type="radio" id={`${name}_${value}`} name={name} value={value} onChange={handleChange} checked={formData[name] === value} />
      <label htmlFor={`${name}_${value}`} className={`radio-btn ${formData[name] === value ? 'active' : ''}`}>{label}</label>
    </>
  );

  return (
    <>
      <div className="navbar">
        <div className="navbar-left">
          {/* Replaced the 'L' placeholder with your actual logo */}
          <img src="/logo.png" alt="Company Logo" className="logo-img" />
          <div className="logo-text">⚡ Lead Intelligence Engine</div>
        </div>
        <div className="navbar-right">User: <b style={{ color: '#1e293b' }}>Sales Team</b></div>
      </div>

      <div className="main">
        {/* LEFT PANEL */}
        <div className="panel left">
          <div className="left-scrollable">

            {/* AI Model */}
            <div className="section">
              <div className="section-title">⚙️ AI Model</div>
              <div className="button-group">
                <RadioBtn name="model" value="meta-llama/llama-3.3-70b-instruct" label="Llama 3.3 70B" />
                <RadioBtn name="model" value="mistralai/mistral-nemo" label="Mistral Nemo" />
                <RadioBtn name="model" value="liquid/lfm-2-24b-a2b" label="LFM 2 24B" />
              </div>
            </div>

            {/* Target Profile */}
            <div className="section">
              <div className="section-title">🎯 1. Target Profile</div>
              <div className="row">
                <input type="text" name="company_name" value={formData.company_name} onChange={handleChange} placeholder="Company Name *" />
                <input type="text" name="website" value={formData.website} onChange={handleChange} placeholder="Website *" />
              </div>
              <div className="row">
                <input type="text" name="linkedin_url" value={formData.linkedin_url} onChange={handleChange} placeholder="LinkedIn URL (opt.)" />
                <SearchableSelect placeholder="Country" options={countries} value={formData.country} onChange={(v) => setFormData({...formData, country: v})} />
              </div>
              <SearchableSelect 
                placeholder="Industry Domain" 
                value={formData.industry} 
                onChange={(v) => setFormData({...formData, industry: v})}
                options={[
                  { label: "Banking & Finance", value: "Banking & Financial Services" },
                  { label: "Insurance", value: "Insurance" },
                  { label: "Healthcare & Pharma", value: "Healthcare & Pharma" },
                  { label: "Manufacturing & OT", value: "Manufacturing & OT / ICS" },
                  { label: "IT & SaaS", value: "Information Technology & SaaS" },
                  { label: "Telecom & Media", value: "Telecom & Media" },
                  { label: "Retail & E-Commerce", value: "Retail & E-Commerce" },
                  { label: "Logistics", value: "Logistics & Supply Chain" },
                  { label: "Energy & Utilities", value: "Energy & Utilities" },
                  { label: "Education & EdTech", value: "Education & EdTech" },
                  { label: "Government & Defence", value: "Government & Defence" },
                  { label: "Legal & Compliance", value: "Legal & Compliance" },
                  { label: "Real Estate", value: "Real Estate & Construction" },
                  { label: "Hospitality & Travel", value: "Hospitality & Travel" },
                  { label: "Other", value: "Other" }
                ]} 
              />
            </div>

            {/* Service Scope */}
            <div className="section">
              <div className="section-title">🛡️ 2. Service Scope</div>
              <div className="button-group">
                <RadioBtn name="service_type" value="Web VAPT" label="Web VAPT" />
                <RadioBtn name="service_type" value="API VAPT" label="API VAPT" />
                <RadioBtn name="service_type" value="Network VAPT" label="Net VAPT" />
              </div>
              <div className="row">
                <input type="number" name="scope_count" value={formData.scope_count} onChange={handleChange} min="1" placeholder="IPs / Apps / Endpoints" />
                <select name="complexity" value={formData.complexity} onChange={handleChange}>
                  <option value="Low">Complexity: Low</option>
                  <option value="Medium">Complexity: Medium</option>
                  <option value="High">Complexity: High</option>
                </select>
              </div>
            </div>

            {/* Behavioral Signals */}
            <details className="accordion">
              <summary className="accordion-header">🧠 3. Behavioral Signals</summary>
              <div className="accordion-content">
                <div className="signal-grid">
                  <div className="signal-item">
                    <div className="signal-label">Lead Type</div>
                    <div className="button-group">
                      <RadioBtn name="lead_type" value="New" label="New" />
                      <RadioBtn name="lead_type" value="Repeat" label="Repeat" />
                      <RadioBtn name="lead_type" value="Referred" label="Ref." />
                    </div>
                  </div>
                  <div className="signal-item">
                    <div className="signal-label">Requirement Clarity</div>
                    <div className="button-group">
                      <RadioBtn name="clarity" value="Vague" label="Vague" />
                      <RadioBtn name="clarity" value="Somewhat clear" label="Partial" />
                      <RadioBtn name="clarity" value="Clear" label="Clear" />
                    </div>
                  </div>
                  <div className="signal-item">
                    <div className="signal-label">Buying Stage</div>
                    <div className="button-group">
                      <RadioBtn name="buying_stage" value="Exploring" label="Exploring" />
                      <RadioBtn name="buying_stage" value="Comparing" label="Comparing" />
                      <RadioBtn name="buying_stage" value="Ready" label="Ready" />
                    </div>
                  </div>
                  <div className="signal-item">
                    <div className="signal-label">Price Sensitivity</div>
                    <div className="button-group">
                      <RadioBtn name="price_sensitivity" value="Low" label="Low" />
                      <RadioBtn name="price_sensitivity" value="Medium" label="Med" />
                      <RadioBtn name="price_sensitivity" value="High" label="High" />
                    </div>
                  </div>
                  <div className="signal-item">
                    <div className="signal-label">Expected Timeline</div>
                    <div className="button-group">
                      <RadioBtn name="timeline" value="Immediate (1-2 weeks)" label="Immediate" />
                      <RadioBtn name="timeline" value="Soon (1 month)" label="Soon" />
                      <RadioBtn name="timeline" value="Not Defined" label="Not Defined" />
                    </div>
                  </div>
                  <div className="signal-item">
                    <div className="signal-label">Competition</div>
                    <div className="checkbox-item" style={{ height: '38px' }}>
                      <input type="checkbox" id="competitors" name="competitors" checked={formData.competitors} onChange={handleChange} />
                      <label htmlFor="competitors">Other Vendors Involved?</label>
                    </div>
                  </div>
                </div>
              </div>
            </details>

            {/* Pricing Rules */}
            <details className="accordion">
              <summary className="accordion-header">💰 4. Pricing Rules & Overrides</summary>
              <div className="accordion-content">
                <div className="row-3">
                  <div><div className="signal-label">Web Base</div><input type="number" name="web_rate" value={formData.web_rate} onChange={handleChange} /></div>
                  <div><div className="signal-label">API Base</div><input type="number" name="api_rate" value={formData.api_rate} onChange={handleChange} /></div>
                  <div><div className="signal-label">Net Base</div><input type="number" name="net_rate" value={formData.net_rate} onChange={handleChange} /></div>
                </div>
                <div className="row">
                  <div><div className="signal-label">Min Quote (₹)</div><input type="number" name="min_value" value={formData.min_value} onChange={handleChange} /></div>
                  <div><div className="signal-label">Ent. Multiplier</div><input type="number" name="ent_mult" value={formData.ent_mult} onChange={handleChange} step="0.1" /></div>
                </div>
              </div>
            </details>
          </div>

          <button className="btn-analyze" onClick={handleAnalyze} disabled={loading}>
            {loading ? "Analyzing… ⏳" : "Run Intelligence Sweep 🚀"}
          </button>

          <div id="debug-console" style={{ display: debugLog === "" ? 'none' : 'block' }}>
            <strong style={{ color: '#fff' }}>[SYSTEM DEBUG]</strong><br />
            <hr style={{ borderColor: '#334155', margin: '5px 0' }} />
            <span id="debug-output">{debugLog}</span>
          </div>
        </div>

        {/* RIGHT PANEL */}
        <div className="panel right">
          <div className="tabs">
            <button className={`tab ${activeTab === 'dash' ? 'active' : ''}`} onClick={() => handleTabSwitch('dash')}>🎯 Live Deal Desk</button>
            <button className={`tab ${activeTab === 'hist' ? 'active' : ''}`} onClick={() => handleTabSwitch('hist')}>📚 Intelligence History</button>
          </div>

          {activeTab === 'dash' && (
            <div className="content">
              {!loading && !aiResult && (
                <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: '10px', color: '#94a3b8' }}>
                  <div style={{ fontSize: '32px' }}>🎯</div>
                  <div style={{ fontSize: '13px', fontWeight: '500' }}>Configure profile on the left and run sweep.</div>
                </div>
              )}

              {loading && (
                <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '14px' }}>
                  <div className="spinner"></div>
                  <div style={{ color: '#64748b', fontWeight: '600', fontSize: '13px' }}>Gathering OSINT & running AI inference…</div>
                  <div style={{ color: '#94a3b8', fontSize: '11px' }}>This takes ~30–60 seconds</div>
                </div>
              )}

              {aiResult && aiResult._error && (
                <div style={{ padding: '20px', color: '#ef4444', fontWeight: '600' }}>Server Error: {aiResult._error}</div>
              )}

              {aiResult && !aiResult._error && !loading && (
                <div style={{ overflowY: 'auto', height: '100%', paddingRight: '6px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
                  
                  {aiResult.previous_enquiry && (
                    <div style={{ background: '#fffbeb', border: '1px solid #fde68a', padding: '10px 14px', borderRadius: '8px', fontSize: '12px', display: 'flex', alignItems: 'center', gap: '12px', flexShrink: 0 }}>
                      <span style={{ fontSize: '22px' }}>🔄</span>
                      <div>
                        <div style={{ fontWeight: '700', color: '#b45309', fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>Repeat Lead Detected</div>
                        <div style={{ color: '#78350f', marginTop: '2px' }}><b>{resultMeta.companyName}</b> enquired <b>{aiResult.previous_enquiry.time_ago}</b> — Past decision: <b>{aiResult.previous_enquiry.decision}</b> · Quote: <b>₹{Number(aiResult.previous_enquiry.suggested_price || 0).toLocaleString()}</b></div>
                      </div>
                    </div>
                  )}

                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexShrink: 0 }}>
                    <div>
                      <div style={{ fontSize: '18px', fontWeight: '800', color: '#1e293b' }}>{resultMeta.companyName}</div>
                      {resultMeta.website && <a href={resultMeta.website.startsWith('http') ? resultMeta.website : 'https://' + resultMeta.website} target="_blank" rel="noreferrer" style={{ color: '#6366f1', fontSize: '11px', fontWeight: '600', textDecoration: 'none' }}>Visit Website ↗</a>}
                    </div>
                    <div style={{ background: scoreBg(aiResult.lead_score), border: `1px solid ${scoreBorder(aiResult.lead_score)}`, borderRadius: '10px', padding: '10px 18px', textAlign: 'center' }}>
                      <div style={{ fontSize: '11px', fontWeight: '700', color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '2px' }}>Lead Score</div>
                      <div style={{ fontSize: '22px', fontWeight: '800', color: scoreColor(aiResult.lead_score) }}>{aiResult.lead_score || 'N/A'}</div>
                      <div style={{ fontSize: '11px', color: '#64748b' }}>{aiResult.conversion_probability || '?'}% conversion</div>
                    </div>
                  </div>

                  <div className="r-card" style={{ flexShrink: 0 }}>
                    <div className="r-label">🧠 Reasoning</div>
                    <div style={{ color: '#334155', fontSize: '12px', lineHeight: '1.55' }}>{aiResult.reasoning_summary || aiResult.analysis || 'N/A'}</div>
                  </div>

                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '10px', flexShrink: 0 }}>
                    {[
                      ['Deal Quality', aiResult.deal_quality],
                      ['Confidence', aiResult.confidence],
                      ['Effort Level', aiResult.effort_level]
                    ].map(([label, val]) => (
                      <div key={label} className="r-card" style={{ textAlign: 'center' }}>
                        <div className="r-label">{label}</div>
                        <div style={{ fontSize: '16px', fontWeight: '800', color: '#1e293b' }}>{val || 'N/A'}</div>
                      </div>
                    ))}
                  </div>

                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px', flexShrink: 0 }}>
                    <div className="r-card">
                      <div className="r-label">🔬 Organisation Intelligence</div>
                      {[
                        ['🏢 Size Tier', aiResult.company_profile?.deduced_company_tier],
                        ['👥 Headcount', aiResult.company_profile?.estimated_headcount],
                        ['💰 Financial Status', aiResult.company_profile?.financial_status],
                        ['📅 Years in Business', aiResult.company_profile?.years_in_business],
                        ['📍 Offices', aiResult.company_profile?.office_locations_count],
                        ['🤝 Customer Type', aiResult.company_profile?.customer_type],
                        ['🚀 Startup?', boolLabel(aiResult.company_profile?.is_startup)],
                        ['🎯 Exp. Leadership?', boolLabel(aiResult.company_profile?.leadership_experienced)],
                      ].map(([label, val]) => {
                        const display = (val === null || val === undefined || val === '') ? 'Unknown' : val;
                        return (
                          <div key={label} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '5px 0', borderBottom: '1px solid #f1f5f9' }}>
                            <span style={{ fontSize: '11px', color: '#64748b' }}>{label}</span>
                            <span style={{ fontSize: '11px', ...unkStyle(display) }}>{display}</span>
                          </div>
                        );
                      })}
                    </div>
                    <div className="r-card" style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                      <div style={{ background: '#eff6ff', border: '1px solid #bfdbfe', borderRadius: '8px', padding: '10px' }}>
                        <div className="r-label" style={{ color: '#1e40af' }}>▶️ Next Action</div>
                        <div style={{ fontWeight: '700', color: '#1e3a8a', fontSize: '12px' }}>{aiResult.next_action || 'N/A'}</div>
                      </div>
                      <div>
                        <div className="r-label">💰 Quote</div>
                        <div style={{ fontSize: '22px', fontWeight: '800', color: '#1e293b' }}>₹{Number(aiResult.pricing?.suggested_quote || 0).toLocaleString()}</div>
                        <div style={{ fontSize: '11px', color: '#64748b', marginTop: '2px' }}>Range: ₹{Number(aiResult.pricing?.price_min || 0).toLocaleString()} – ₹{Number(aiResult.pricing?.price_max || 0).toLocaleString()}</div>
                        <div style={{ fontSize: '11px', color: '#64748b', marginTop: '2px' }}>Discount: <b>{aiResult.discount_strategy?.level || 'None'} ({aiResult.discount_strategy?.percentage || 0}%)</b> — {aiResult.discount_strategy?.guidance || ''}</div>
                      </div>
                      <div>
                        <div className="r-label">🗣️ Closing Strategy</div>
                        <ul style={{ paddingLeft: '15px', margin: 0, fontSize: '12px', color: '#334155' }}>
                          {(aiResult.closing_strategy || []).map((s, i) => <li key={i} style={{ marginBottom: '5px' }}>{s}</li>)}
                        </ul>
                      </div>
                    </div>
                  </div>

                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px', flexShrink: 0 }}>
                    <div className="r-card">
                      <div className="r-label" style={{ color: '#dc2626' }}>⚠️ Deal Risks</div>
                      {(aiResult.red_flags || []).filter(f => f && f.toLowerCase() !== 'none').length > 0 ? (
                        (aiResult.red_flags || []).filter(f => f && f.toLowerCase() !== 'none').map((f, i) => (
                          <div key={i} style={{ background: '#fef2f2', border: '1px solid #fecaca', color: '#b91c1c', padding: '7px 10px', borderRadius: '6px', fontSize: '11px', fontWeight: '600', marginBottom: '6px' }}>🚨 {f}</div>
                        ))
                      ) : (
                        <div style={{ color: '#94a3b8', fontSize: '12px' }}>No major risks detected.</div>
                      )}
                    </div>
                    <div className="r-card">
                      <div className="r-label" style={{ color: '#2563eb' }}>🌐 Web Intel</div>
                      {(aiResult.background_insights || []).filter(f => f && f.toLowerCase() !== 'none').length > 0 ? (
                        <ul style={{ paddingLeft: '16px', margin: 0, fontSize: '12px', color: '#334155' }}>
                          {(aiResult.background_insights || []).filter(f => f && f.toLowerCase() !== 'none').map((i, idx) => (
                            <li key={idx} style={{ marginBottom: '5px' }}>{i}</li>
                          ))}
                        </ul>
                      ) : (
                        <div style={{ color: '#94a3b8', fontSize: '12px' }}>No recent digital footprint found.</div>
                      )}
                    </div>
                  </div>
                </div>
              )}
            </div>
          )}

          {activeTab === 'hist' && (
            <div className="content" style={{ overflowY: 'auto' }}>
              <table className="table" id="history-table">
                <thead>
                  <tr>
                    <th>Date / Time</th>
                    <th>Company</th>
                    <th>Service</th>
                    <th>Lead Score</th>
                    <th>Quote (₹)</th>
                  </tr>
                </thead>
                <tbody id="history-tbody">
                  {isHistoryLoading ? (
                    <tr><td colSpan="5" style={{ textAlign: 'center', color: '#94a3b8', padding: '30px' }}>Loading history…</td></tr>
                  ) : history.length === 0 ? (
                    <tr><td colSpan="5" style={{ textAlign: 'center', color: '#94a3b8', padding: '30px' }}>No history found. Run your first sweep to save a lead!</td></tr>
                  ) : (
                    history.map((h, i) => (
                      <tr key={i}>
                        <td style={{ color: '#64748b' }}>{new Date(h.timestamp).toLocaleString('en-IN', { dateStyle: 'short', timeStyle: 'short' })}</td>
                        <td style={{ fontWeight: '600' }}>{h.company_name}</td>
                        <td>{h.service_type}</td>
                        <td style={{ fontWeight: '700', color: scoreColor(h.decision) }}>{h.decision}</td>
                        <td style={{ fontWeight: '600' }}>₹{Number(h.suggested_price || 0).toLocaleString()}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      <div className="footer">Copyright © 2026 : Valency Networks Private Limited.</div>
    </>
  );
}