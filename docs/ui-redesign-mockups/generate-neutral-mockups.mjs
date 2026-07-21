import fs from "node:fs";
import path from "node:path";

const out = path.dirname(new URL(import.meta.url).pathname);
const W = 1600, H = 1000;
const C = {
  ink: "#111315", text: "#25282B", muted: "#6B7075", faint: "#92979C",
  line: "#D9DCDE", line2: "#E8EAEB", paper: "#FFFFFF", canvas: "#F4F5F5",
  panel: "#FAFAFA", dark: "#17191B", dark2: "#24272A", white: "#FFFFFF",
  red: "#B4232C", redBg: "#F8EDEE", amber: "#8A5A00", amberBg: "#F7F1E5",
  green: "#386641", greenBg: "#EDF4EE", blue: "#315A72", blueBg: "#EDF2F5",
};
const esc = s => String(s).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
const rect = (x,y,w,h,fill=C.paper,stroke="none",r=0,sw=1) =>
  `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${r}" fill="${fill}" stroke="${stroke}" stroke-width="${sw}"/>`;
const line = (x1,y1,x2,y2,stroke=C.line,sw=1,dash="") =>
  `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${stroke}" stroke-width="${sw}"${dash?` stroke-dasharray="${dash}"`:""}/>`;
const text = (x,y,s,size=14,weight=400,fill=C.text,anchor="start",spacing=0) =>
  `<text x="${x}" y="${y}" font-size="${size}" font-weight="${weight}" fill="${fill}" text-anchor="${anchor}" letter-spacing="${spacing}">${esc(s)}</text>`;
const circle = (cx,cy,r,fill,stroke="none",sw=1) =>
  `<circle cx="${cx}" cy="${cy}" r="${r}" fill="${fill}" stroke="${stroke}" stroke-width="${sw}"/>`;
const status = (x,y,label,tone="neutral") => {
  const map={neutral:[C.panel,C.text,C.line],risk:[C.redBg,C.red,C.redBg],warn:[C.amberBg,C.amber,C.amberBg],ok:[C.greenBg,C.green,C.greenBg],info:[C.blueBg,C.blue,C.blueBg]};
  const [bg,fg,bd]=map[tone]; const w=Math.max(66,label.length*7.2+26);
  return rect(x,y-19,w,28,bg,bd,3)+circle(x+12,y-5,3,fg)+text(x+22,y,label.toUpperCase(),11,650,fg,"start",.5);
};
const button = (x,y,label,primary=false,w=132) =>
  rect(x,y,w,38,primary?C.ink:C.paper,primary?C.ink:C.line,4)+text(x+w/2,y+25,label,13,650,primary?C.white:C.text,"middle");
const metric = (x,y,w,label,value,meta="") =>
  rect(x,y,w,104,C.paper,C.line,5)+text(x+18,y+26,label.toUpperCase(),10,650,C.muted,"start",.8)+
  text(x+18,y+66,value,28,620,C.ink)+text(x+w-18,y+66,meta,12,500,C.muted,"end");
const heading = (x,y,kicker,title,sub="") =>
  text(x,y,kicker.toUpperCase(),11,700,C.muted,"start",1.2)+text(x,y+44,title,32,620,C.ink)+
  (sub?text(x,y+73,sub,14,400,C.muted):"");
const shell = (active,title,kicker,sub,body,{dark=false}={}) => {
  const bg=dark?C.dark:C.canvas, side=dark?"#111315":C.ink, top=dark?C.dark:C.paper;
  const nav=["Overview","Evidence","Decisions","Controls","Costs","Audit"];
  let s=`<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">`;
  s+=`<style>text{font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;font-variant-numeric:tabular-nums}</style>`;
  s+=rect(0,0,W,H,bg)+rect(0,0,228,H,side)+rect(228,0,1372,64,top,dark?C.dark2:C.line2,0);
  s+=text(28,37,"DBX / PLATFORM",14,700,C.white,"start",1.1)+circle(198,32,4,"#778087");
  nav.forEach((n,i)=>{const yy=104+i*48;if(n===active)s+=rect(12,yy-26,204,38,"#2D3033","none",4)+rect(12,yy-26,3,38,C.white);
    s+=text(30,yy,n,13,n===active?650:450,n===active?C.white:"#AEB3B7");});
  s+=line(24,430,204,430,"#34383B")+text(28,464,"WORKSPACE",10,650,"#777E83","start",1)+text(28,490,"Production",13,500,"#D5D8DA");
  s+=circle(28,522,4,"#758A79")+text(42,527,"Sources current",12,450,"#AEB3B7");
  s+=text(28,930,"READ-ONLY CONTROL PLANE",9,650,"#6F767B","start",.8)+text(28,954,"Updated 08:42:16 EDT",11,400,"#8E9498");
  s+=text(258,39,"MISSION CONTROL",11,700,dark?"#AAB0B4":C.muted,"start",1)+text(1564,39,"Search  ⌘K",12,500,dark?"#AAB0B4":C.muted,"end");
  s+=heading(272,112,kicker,title,sub)+body;
  return s+`</svg>`;
};
const tableHeader=(x,y,cols,widths)=>{
  let s=rect(x,y,widths.reduce((a,b)=>a+b,0),36,C.panel,C.line,3);
  let xx=x; cols.forEach((c,i)=>{s+=text(xx+14,y+23,c.toUpperCase(),10,700,C.muted,"start",.7);xx+=widths[i];});
  return s;
};
const tableRow=(x,y,vals,widths,{bold=0,tone=null}={})=>{
  let s=rect(x,y,widths.reduce((a,b)=>a+b,0),54,C.paper,C.line2,0); let xx=x;
  vals.forEach((v,i)=>{s+=text(xx+14,y+33,v,13,i===bold?620:430,tone&&i===0?tone:C.text);xx+=widths[i];});
  return s;
};

let b="";
b+=metric(272,214,238,"Open decisions","03","1 expiring");
b+=metric(526,214,238,"Evidence sources","18 / 18","current");
b+=metric(780,214,238,"Control posture","97.4%","+0.6%");
b+=metric(1034,214,238,"Monthly variance","+$42.8k","2.1%");
b+=metric(1288,214,276,"Last attestation","08:42","run 4218");
b+=rect(272,340,820,548,C.paper,C.line,6)+text(296,374,"DECISION QUEUE",11,700,C.muted,"start",.9)+text(1066,374,"3 items",12,500,C.muted,"end");
b+=tableHeader(296,394,["Priority","Plan","Impact","Window"],[116,326,170,160]);
b+=tableRow(296,430,["P1","Reconcile prod warehouse","+$6.2k / mo","11:48"],[116,326,170,160],{bold:1,tone:C.red});
b+=tableRow(296,484,["P2","Apply cluster policy delta","12 clusters","13:06"],[116,326,170,160],{bold:1});
b+=tableRow(296,538,["P3","Run manual security scan","4 sources","14:31"],[116,326,170,160],{bold:1});
b+=text(296,632,"SELECTED PLAN",10,700,C.muted,"start",.8)+text(296,666,"Reconcile prod warehouse",21,620,C.ink);
b+=text(296,696,"Immutable plan · SHA-256  4f92…c18a · created by CI",13,430,C.muted);
b+=status(296,744,"Human approval","risk")+status(460,744,"Evidence current","ok");
b+=button(296,794,"Review plan",true,138)+button(446,794,"View evidence",false,138);
b+=rect(1112,340,452,548,C.paper,C.line,6)+text(1138,374,"OPERATING SIGNAL",11,700,C.muted,"start",.9);
b+=text(1138,430,"No active incident",25,600,C.ink)+status(1138,470,"Stable","ok");
b+=line(1138,510,1538,510)+text(1138,546,"Evidence freshness",12,500,C.muted)+text(1538,546,"100%",13,650,C.text,"end");
b+=line(1138,568,1538,568)+text(1138,604,"Failed scheduled checks",12,500,C.muted)+text(1538,604,"0",13,650,C.text,"end");
b+=line(1138,626,1538,626)+text(1138,662,"Unverified executions",12,500,C.muted)+text(1538,662,"0",13,650,C.text,"end");
b+=text(1138,710,"7-DAY CONTROL COVERAGE",10,700,C.muted,"start",.8);
[86,91,89,94,96,96,97].forEach((v,i)=>{b+=rect(1138+i*55,840-v,32,v,"#3A3E41","none",2);});
fs.writeFileSync(path.join(out,"11-otpp-command-ribbon.svg"),shell("Overview","Command overview","Production workspace","Decisions, evidence, and control posture at 08:42 EDT.",b));

b="";
b+=rect(272,214,1292,674,C.paper,C.line,6)+text(296,250,"EVIDENCE REGISTER",11,700,C.muted,"start",.9);
b+=button(1396,228,"Export index",false,142);
b+=tableHeader(296,276,["Source","Domain","Freshness","Last attested","Coverage","Run"],[326,190,162,210,170,186]);
const ev=[
["system.billing.usage","Cost","CURRENT","08:40:12","100%","job/4218"],
["system.access.audit","Security","CURRENT","08:38:55","100%","job/4217"],
["system.compute.clusters","Governance","CURRENT","08:37:06","98.6%","job/4216"],
["azure.costmanagement","Cloud cost","CURRENT","08:31:44","100%","job/4215"],
["policies/standard.json","Policy source","VERIFIED","commit 8c21d6","12 / 12","bundle"],
["audit.execution_events","Action history","APPEND ONLY","08:42:01","100%","stream"],
];
ev.forEach((r,i)=>b+=tableRow(296,312+i*62,r,[326,190,162,210,170,186],{bold:0}));
b+=rect(296,706,1244,150,C.panel,C.line,4)+text(318,738,"SOURCE HEALTH NOTE",10,700,C.muted,"start",.8);
b+=text(318,774,"Coverage for system.compute.clusters is 98.6%. Two terminated clusters are outside the active scan window.",14,450,C.text);
b+=text(318,806,"This does not block current findings. Open the run attestation for scope and query provenance.",13,400,C.muted);
b+=button(1312,762,"Open attestation",true,202);
fs.writeFileSync(path.join(out,"12-otpp-evidence-cards.svg"),shell("Evidence","Evidence register","Traceability","Source health, freshness, query provenance, and exact run attestations.",b));

b="";
b+=rect(272,214,1292,92,C.paper,C.line,6);
["1  Evidence","2  Immutable plan","3  Approval","4  Confirmation","5  Execution","6  Verification"].forEach((s,i)=>{
  const x=292+i*207; b+=circle(x,260,11,i<2?C.ink:C.paper,C.line,1)+text(x+20,265,s,12,i===2?650:450,i===2?C.ink:C.muted);
  if(i<5)b+=line(x+132,260,x+192,260,C.line);
});
b+=rect(272,326,850,562,C.paper,C.line,6)+text(298,362,"PLAN UNDER REVIEW",10,700,C.muted,"start",.8);
b+=text(298,405,"Reconcile production SQL warehouse",25,620,C.ink);
b+=status(298,446,"Approval required","risk")+status(462,446,"Expires 11:48","warn");
b+=line(298,474,1096,474);
const fields=[["Plan hash","4f92c0bd…c18a"],["Target","warehouse / dbx-platform-prod"],["Proposed state","RUNNING · auto-stop 10 min"],["Executor","spn-dbxp-warehouse-executor"],["Created","2026-07-19 08:29:54 EDT"]];
fields.forEach((r,i)=>{b+=text(298,516+i*48,r[0],12,500,C.muted);b+=text(504,516+i*48,r[1],13,550,C.text);});
b+=rect(298,772,798,86,C.panel,C.line,4)+text(318,802,"FAIL-CLOSED CONDITIONS",10,700,C.muted,"start",.8)+text(318,830,"Hash mismatch · drift · expired window · replay · missing identity or audit storage",13,430,C.text);
b+=rect(1142,326,422,562,C.paper,C.line,6)+text(1168,362,"REVIEW",10,700,C.muted,"start",.8);
b+=text(1168,406,"Separate confirmation required",19,600,C.ink)+text(1168,438,"Approval does not execute the plan.",13,430,C.muted);
b+=rect(1168,478,370,112,C.panel,C.line,4)+text(1188,508,"APPROVER ELIGIBILITY",10,700,C.muted,"start",.8)+status(1188,550,"Membership current","ok");
b+=text(1168,642,"Approval expires in",12,500,C.muted)+text(1168,688,"11:48",42,600,C.ink);
b+=button(1168,742,"Approve plan",true,174)+button(1354,742,"Decline",false,138);
b+=text(1168,814,"Next: review the exact confirmation phrase.",12,400,C.muted);
fs.writeFileSync(path.join(out,"13-otpp-approval-stage.svg"),shell("Decisions","Approval review","Plan 4f92c0bd","Exact scope, immutable payload, identity, expiry, and fail-closed conditions.",b));

b="";
b+=metric(272,214,238,"Resources assessed","186","all domains")+metric(526,214,238,"Policy coverage","97.4%","+0.6%")+metric(780,214,238,"Exceptions","04","2 expiring")+metric(1034,214,238,"Open decisions","03","1 urgent");
b+=rect(272,340,840,548,C.paper,C.line,6)+text(296,376,"CONTROL COVERAGE BY DOMAIN",11,700,C.muted,"start",.9);
const domains=["Compute","Identity","Storage","Jobs","Models","Networking"], vals=[97,100,94,99,92,96];
domains.forEach((d,i)=>{const y=426+i*66;b+=text(296,y,d,13,550,C.text);b+=rect(430,y-17,600,20,C.line2,"none",2);b+=rect(430,y-17,vals[i]*6,20,vals[i]<95?"#6F7376":"#313538","none",2);b+=text(1066,y,`${vals[i]}%`,12,650,C.text,"end");});
b+=text(296,836,"Coverage is evidence-backed; unknown scope is not counted as compliant.",12,400,C.muted);
b+=rect(1132,214,432,674,C.paper,C.line,6)+text(1158,250,"EXCEPTION REGISTER",11,700,C.muted,"start",.9);
const ex=[["POL-017","2 clusters","09 days","risk"],["POL-031","1 model","21 days","warn"],["POL-044","1 job","44 days","neutral"]];
ex.forEach((r,i)=>{const y=304+i*126;b+=text(1158,y,r[0],12,700,C.ink)+status(1260,y,r[3]==="risk"?"High":"Review",r[3]);b+=text(1158,y+35,r[1],14,500,C.text)+text(1538,y+35,r[2],12,500,C.muted,"end");b+=line(1158,y+66,1538,y+66);});
b+=text(1158,694,"COMMITTEE NOTE",10,700,C.muted,"start",.8)+text(1158,728,"Two exceptions expire before",13,450,C.text)+text(1158,750,"the next governance review.",13,450,C.text)+button(1158,798,"Review exceptions",true,194);
fs.writeFileSync(path.join(out,"14-otpp-portfolio-heatmap.svg"),shell("Controls","Control portfolio","Governance view","Coverage, exceptions, expiry, and evidence confidence across the workspace.",b));

b="";
b+=rect(272,214,736,674,C.paper,C.line,6)+text(298,250,"INVESTIGATION",11,700,C.muted,"start",.9);
b+=rect(298,280,684,74,C.panel,C.line,4)+text(318,310,"YOU",10,700,C.muted,"start",.8)+text(318,336,"Why did compute cost rise this week?",14,500,C.text);
b+=text(298,402,"ASSISTANT · READ ONLY",10,700,C.muted,"start",.8);
b+=text(298,438,"Compute cost increased 8.2% week over week.",18,600,C.ink);
b+=text(298,470,"The increase is concentrated in three interactive clusters.",14,430,C.text);
b+=text(298,498,"Two show idle time above the 30% policy threshold.",14,430,C.text);
b+=rect(298,530,684,116,C.panel,C.line,4)+text(318,560,"SUPPORTED BY",10,700,C.muted,"start",.8)+text(318,590,"[1] system.billing.usage · run 4218 · 08:40",13,500,C.blue)+text(318,618,"[2] system.compute.clusters · run 4216 · 08:37",13,500,C.blue);
b+=rect(298,792,684,58,C.paper,C.line,4)+text(318,827,"Ask about current evidence…",13,400,C.faint)+text(958,827,"↵",16,600,C.muted,"end");
b+=rect(1028,214,536,674,C.paper,C.line,6)+text(1054,250,"CITED EVIDENCE",11,700,C.muted,"start",.9);
const cites=[["01","Compute usage","+8.2% WoW","CURRENT"],["02","Idle cluster hours","184.6 h","CURRENT"],["03","Estimated avoidable","$11.4k / mo","MODELLED"]];
cites.forEach((r,i)=>{const y=292+i*154;b+=rect(1054,y,484,126,C.panel,C.line,4)+text(1074,y+30,r[0],11,700,C.muted)+text(1118,y+30,r[1],14,600,C.ink)+text(1118,y+64,r[2],20,600,C.text)+status(1118,y+100,r[3],r[3]==="CURRENT"?"ok":"info");});
b+=rect(1054,770,484,80,C.redBg,C.redBg,4)+text(1074,800,"EXECUTION BOUNDARY",10,700,C.red,"start",.8)+text(1074,828,"Can draft a proposal; cannot execute changes.",13,500,C.text);
fs.writeFileSync(path.join(out,"15-otpp-agent-copilot.svg"),shell("Evidence","Evidence assistant","Read-only investigation","Answers cite current workspace evidence and preserve the execution boundary.",b));

b="";
b+=text(272,242,"SUNDAY, 19 JULY 2026",12,700,C.muted,"start",1.1)+text(272,310,"The workspace is stable.",42,560,C.ink);
b+=text(272,348,"Three decisions need review; one expires before 09:00.",19,430,C.muted);
b+=line(272,388,1564,388,C.line);
b+=text(272,430,"01",13,700,C.muted)+text(326,430,"DECISIONS",11,700,C.muted,"start",.9)+text(326,474,"3",48,560,C.ink)+text(410,472,"open",15,500,C.muted);
b+=text(326,520,"Warehouse reconciliation expires in 11:48.",14,500,C.text)+button(326,554,"Review queue",true,144);
b+=line(690,414,690,636,C.line);
b+=text(730,430,"02",13,700,C.muted)+text(784,430,"CONTROL POSTURE",11,700,C.muted,"start",.9)+text(784,474,"97.4%",48,560,C.ink)+text(784,520,"Four documented exceptions. No unknown scope.",14,500,C.text);
b+=line(1150,414,1150,636,C.line);
b+=text(1190,430,"03",13,700,C.muted)+text(1244,430,"COST",11,700,C.muted,"start",.9)+text(1244,474,"+2.1%",48,560,C.ink)+text(1244,520,"Month-to-date variance; compute is the driver.",14,500,C.text);
b+=line(272,666,1564,666,C.line);
b+=text(272,710,"OVERNIGHT ACTIVITY",11,700,C.muted,"start",.9);
const acts=[["08:40","Cost evidence attested","job 4218"],["08:38","Security evidence attested","job 4217"],["07:15","Budget forecast refreshed","job 4211"]];
acts.forEach((r,i)=>{const y=754+i*44;b+=text(272,y,r[0],12,600,C.muted)+text(350,y,r[1],13,500,C.text)+text(764,y,r[2],12,450,C.muted);});
b+=rect(1110,712,454,138,C.panel,C.line,4)+text(1132,742,"NEXT SCHEDULED CHECK",10,700,C.muted,"start",.8)+text(1132,782,"Security posture",18,600,C.ink)+text(1132,816,"09:00 EDT · schedule paused in dev",12,450,C.muted);
fs.writeFileSync(path.join(out,"16-otpp-morning-brief.svg"),shell("Overview","Morning brief","Executive summary","A concise operating brief for the production workspace.",b));

b="";
b+=rect(272,214,1292,86,C.dark2,"#34383B",5)+circle(302,257,6,C.red)+text(324,250,"ACTIVE INVESTIGATION",10,700,"#AEB3B7","start",.9)+text(324,274,"Security audit source lag",18,600,C.white)+status(1338,263,"SEV 2","risk");
b+=rect(272,320,816,568,C.dark2,"#34383B",5)+text(298,354,"SIGNAL TIMELINE",11,700,"#AEB3B7","start",.9);
const pts=[[330,0],[390,10],[450,5],[510,22],[570,16],[630,74],[690,44],[750,31],[810,26],[870,18],[930,13],[990,9]];
b+=`<polyline points="${pts.map(p=>`${p[0]},${610-p[1]*2.4}`).join(" ")}" fill="none" stroke="#E3E5E6" stroke-width="2"/>`;
pts.forEach((p,i)=>b+=circle(p[0],610-p[1]*2.4,i===5?5:3,i===5?C.red:"#8C9398"));
b+=line(298,640,1062,640,"#454A4E")+text(298,674,"08:12",11,450,"#92999E")+text(1038,674,"08:42",11,450,"#92999E","end");
b+=text(298,728,"CORRELATED EVIDENCE",10,700,"#AEB3B7","start",.8);
b+=text(298,762,"Audit ingest delay",13,500,C.white)+text(760,762,"18m 42s",13,600,C.white,"end")+status(824,762,"Degraded","risk");
b+=line(298,784,1062,784,"#3A3F42")+text(298,820,"Other workspace sources",13,500,C.white)+text(760,820,"17 / 17",13,600,C.white,"end")+status(824,820,"Current","ok");
b+=rect(1108,320,456,568,C.dark2,"#34383B",5)+text(1134,354,"RESPONSE LOG",11,700,"#AEB3B7","start",.9);
const logs=[["08:24","Threshold crossed"],["08:27","Source retry started"],["08:31","On-call acknowledged"],["08:38","Backlog decreasing"]];
logs.forEach((r,i)=>{const y=406+i*70;b+=circle(1140,y-4,4,i===0?C.red:"#899095");b+=line(1140,y+5,1140,y+52,"#4A4F52");b+=text(1160,y,r[0],11,600,"#9EA4A8")+text(1224,y,r[1],13,500,C.white);});
b+=rect(1134,706,404,112,"#1D1F21","#414548",4)+text(1154,738,"EXECUTION STATE",10,700,"#9EA4A8","start",.8)+text(1154,770,"No target mutation proposed",14,500,C.white)+text(1154,796,"Monitoring and evidence append only",12,430,"#AEB3B7");
fs.writeFileSync(path.join(out,"17-otpp-incident-pulse.svg"),shell("Overview","Incident pulse","Live operations","Evidence source degradation · investigation opened 08:24 EDT.",b,{dark:true}));

b="";
b+=metric(272,214,238,"Month to date","$2.08m","+2.1%")+metric(526,214,238,"Forecast","$3.19m","within budget")+metric(780,214,238,"Avoidable estimate","$38.4k","modelled")+metric(1034,214,238,"Unattributed","0.7%","-0.2%");
b+=rect(272,340,800,548,C.paper,C.line,6)+text(298,376,"DAILY SPEND",11,700,C.muted,"start",.9);
const costVals=[54,59,57,63,61,72,69,76,81,79,84,88,86,91,96,92,99,104,101];
const points=costVals.map((v,i)=>`${312+i*38},${700-v*2.6}`).join(" ");
b+=`<polyline points="${points}" fill="none" stroke="#25282B" stroke-width="2"/>`;
costVals.forEach((v,i)=>b+=circle(312+i*38,700-v*2.6,2.5,C.ink));
b+=line(298,462,1046,462,C.line2,1,"4 4")+line(298,570,1046,570,C.line2,1,"4 4")+line(298,700,1046,700,C.line2);
b+=text(298,744,"01 JUL",10,600,C.muted)+text(1046,744,"19 JUL",10,600,C.muted,"end");
b+=rect(298,786,748,70,C.panel,C.line,4)+text(318,814,"FORECAST NOTE",10,700,C.muted,"start",.8)+text(318,839,"Current trajectory remains $126k below the approved monthly budget.",13,450,C.text);
b+=rect(1092,214,472,674,C.paper,C.line,6)+text(1118,250,"VARIANCE DRIVERS",11,700,C.muted,"start",.9);
const drivers=[["Interactive compute","+$52.6k","8.2%"],["Jobs compute","-$11.8k","-2.4%"],["SQL warehouse","+$6.2k","3.1%"],["Model serving","-$4.2k","-1.8%"]];
drivers.forEach((r,i)=>{const y=308+i*82;b+=text(1118,y,r[0],13,550,C.text)+text(1538,y,r[1],14,650,r[1][0]==="+"?C.ink:C.green,"end");b+=text(1538,y+25,r[2],11,500,C.muted,"end");b+=line(1118,y+46,1538,y+46);});
b+=text(1118,674,"PROPOSED ACTION",10,700,C.muted,"start",.8)+text(1118,710,"Draft idle-cluster review",17,600,C.ink)+text(1118,740,"Estimate only · approval required",12,450,C.muted)+button(1118,786,"Draft proposal",true,170);
fs.writeFileSync(path.join(out,"18-otpp-cost-lens.svg"),shell("Costs","Cost lens","Financial control","Attributed spend, forecast variance, and evidence-backed opportunities.",b));

b="";
b+=rect(272,214,1292,674,C.paper,C.line,6)+text(298,250,"POLICY SOURCE",11,700,C.muted,"start",.9)+text(1538,250,"commit 8c21d6 · verified",12,500,C.muted,"end");
const nodes=[
  [298,294,300,104,"BASELINE","standard.json","12 assigned"],
  [654,294,300,104,"COMPUTE","compute-secure.json","8 assigned"],
  [1010,294,300,104,"ML","model-serving.json","4 assigned"],
];
nodes.forEach(n=>{b+=rect(n[0],n[1],n[2],n[3],C.panel,C.line,4)+text(n[0]+18,n[1]+27,n[4],10,700,C.muted,"start",.8)+text(n[0]+18,n[1]+58,n[5],15,600,C.ink)+text(n[0]+18,n[1]+84,n[6],12,450,C.muted);});
b+=line(448,398,448,450,C.line,2)+line(804,398,804,450,C.line,2)+line(1160,398,1160,450,C.line,2)+line(448,450,1160,450,C.line,2);
const controls=[["POL-011","Photon required","COMPLIANT","12 / 12"],["POL-017","Runtime allowlist","EXCEPTION","10 / 12"],["POL-031","Auto-termination","REVIEW","11 / 12"],["POL-044","Data security mode","COMPLIANT","12 / 12"]];
b+=tableHeader(298,484,["Control","Requirement","State","Coverage"],[150,444,180,220]);
controls.forEach((r,i)=>b+=tableRow(298,520+i*58,r,[150,444,180,220],{bold:0,tone:r[2]==="EXCEPTION"?C.red:null}));
b+=rect(1318,294,220,458,C.panel,C.line,4)+text(1340,326,"LEGEND",10,700,C.muted,"start",.8);
b+=status(1340,370,"Compliant","ok")+status(1340,424,"Review","warn")+status(1340,478,"Exception","risk");
b+=line(1340,524,1516,524)+text(1340,560,"SYNC STATUS",10,700,C.muted,"start",.8)+text(1340,594,"Source verified",13,550,C.text)+text(1340,620,"Workspace drift: 0",13,550,C.text)+text(1340,646,"Last scan 08:37",12,450,C.muted);
b+=text(298,800,"Policy changes require an immutable reconciliation plan and dedicated executor.",12,450,C.muted)+button(1328,806,"View source",true,190);
fs.writeFileSync(path.join(out,"19-otpp-policy-map.svg"),shell("Controls","Policy map","Policy as code","Source hierarchy, assignment coverage, exceptions, and workspace drift.",b));

b="";
b+=rect(272,214,1292,226,C.paper,C.line,6)+circle(326,282,24,C.greenBg)+text(326,290,"✓",22,700,C.green,"middle");
b+=text(372,276,"No actionable findings",28,600,C.ink)+text(372,310,"All scheduled evidence checks completed within their expected windows.",14,430,C.muted);
b+=status(372,354,"18 sources current","ok")+status(530,354,"0 failed checks","ok")+text(1538,354,"Attested 08:42:16 EDT",12,450,C.muted,"end");
b+=text(272,486,"WHAT WAS CHECKED",11,700,C.muted,"start",.9);
const checks=[["Security","0 findings","08:38"],["Governance","0 drift","08:37"],["Housekeeping","0 candidates","08:35"],["AI catalog","0 exceptions","08:34"]];
checks.forEach((r,i)=>{const x=272+i*322;b+=rect(x,510,304,138,C.paper,C.line,5)+text(x+18,540,r[0].toUpperCase(),10,700,C.muted,"start",.8)+text(x+18,580,r[1],20,600,C.ink)+text(x+18,616,`Attested ${r[2]}`,12,450,C.muted);});
b+=rect(272,686,832,202,C.paper,C.line,6)+text(298,722,"REMAINING WORK",11,700,C.muted,"start",.9);
b+=text(298,764,"03",28,600,C.ink)+text(352,762,"approved decisions awaiting review",14,500,C.text)+text(1032,762,"Open queue →",13,650,C.text,"end");
b+=line(298,790,1078,790)+text(298,830,"01",28,600,C.ink)+text(352,828,"scheduled source refresh in the next hour",14,500,C.text)+text(1032,828,"09:00 EDT",13,500,C.muted,"end");
b+=rect(1124,686,440,202,C.panel,C.line,6)+text(1150,722,"NEXT ATTESTATION",11,700,C.muted,"start",.9)+text(1150,766,"Security posture",21,600,C.ink)+text(1150,800,"Scheduled 09:00 EDT",13,450,C.muted)+button(1150,830,"View schedule",false,156);
fs.writeFileSync(path.join(out,"20-otpp-calm-zero.svg"),shell("Overview","Workspace health","Healthy state","Zero findings with explicit coverage, freshness, and remaining work.",b));
