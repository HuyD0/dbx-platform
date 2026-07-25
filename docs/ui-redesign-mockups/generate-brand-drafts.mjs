import fs from "node:fs";
import path from "node:path";

const out = path.dirname(new URL(import.meta.url).pathname);
const P = {
  red: "#E31B3D", redDark: "#9D1634", maroon: "#2A0D18", burgundy: "#4B1628",
  rose: "#F5E9EC", blush: "#FBF6F7", cream: "#F7F3F1", gold: "#C49A55",
  ink: "#151515", charcoal: "#292929", grey: "#6C6C6C", pale: "#F2F2F1",
  line: "#D8D5D4", white: "#FFFFFF", green: "#426A4A",
};
const e = s => String(s).replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;");
const r = (x,y,w,h,fill,stroke="none",rx=0,sw=1) =>
  `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${rx}" fill="${fill}" stroke="${stroke}" stroke-width="${sw}"/>`;
const t = (x,y,s,size=14,weight=400,fill=P.ink,anchor="start",ls=0) =>
  `<text x="${x}" y="${y}" font-size="${size}" font-weight="${weight}" fill="${fill}" text-anchor="${anchor}" letter-spacing="${ls}">${e(s)}</text>`;
const l = (x1,y1,x2,y2,stroke=P.line,sw=1,dash="") =>
  `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${stroke}" stroke-width="${sw}"${dash?` stroke-dasharray="${dash}"`:""}/>`;
const c = (x,y,rad,fill,stroke="none") => `<circle cx="${x}" cy="${y}" r="${rad}" fill="${fill}" stroke="${stroke}"/>`;
const start = bg => `<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="1000" viewBox="0 0 1600 1000"><style>text{font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;font-variant-numeric:tabular-nums}</style>${r(0,0,1600,1000,bg)}`;
const end = "</svg>";
const tag = (x,y,label,fg=P.red,bg=P.rose) => {
  const w=label.length*7+25; return r(x,y-19,w,28,bg,"none",2)+c(x+11,y-5,3,fg)+t(x+21,y,label.toUpperCase(),10,700,fg,"start",.6);
};
const btn = (x,y,label,dark=true,w=146,brand=P.maroon) =>
  r(x,y,w,38,dark?brand:P.white,dark?brand:P.line,3)+t(x+w/2,y+25,label,12,650,dark?P.white:P.ink,"middle");
const navItems = (x,y,active,light=false) => {
  const items=["Overview","Evidence","Decisions","Controls","Costs","Audit"]; let s="";
  items.forEach((item,i)=>{const yy=y+i*49;
    if(item===active)s+=r(x,yy-27,188,38,light?P.rose:"#FFFFFF16","none",2)+r(x,yy-27,3,38,light?P.red:P.white);
    s+=t(x+18,yy,item,13,item===active?650:430,light?(item===active?P.maroon:P.grey):(item===active?P.white:"#C8BDC1"));
  }); return s;
};
const commonTable = (x,y,w,brand=P.red,dark=false) => {
  const bg=dark?"#31131F":P.white, fg=dark?P.white:P.ink, linec=dark?"#573040":P.line;
  let s=r(x,y,w,278,bg,linec,4)+t(x+22,y+31,"DECISION QUEUE",10,700,dark?"#CDBFC4":P.grey,"start",.9);
  s+=t(x+w-22,y+31,"3 OPEN",10,700,dark?"#CDBFC4":P.grey,"end",.8)+l(x+22,y+52,x+w-22,y+52,linec);
  [["P1","Reconcile production warehouse","+$6.2k / mo","11:48"],
   ["P2","Apply cluster policy delta","12 clusters","13:06"],
   ["P3","Run manual security scan","4 sources","14:31"]].forEach((q,i)=>{
    const yy=y+88+i*58; s+=t(x+22,yy,q[0],12,700,i===0?brand:fg)+t(x+82,yy,q[1],13,600,fg);
    s+=t(x+w-150,yy,q[2],12,450,dark?"#D8CFD2":P.grey,"end")+t(x+w-22,yy,q[3],12,600,fg,"end");
    if(i<2)s+=l(x+22,yy+24,x+w-22,yy+24,linec);
  }); return s;
};
const metrics = (x,y,widths,variant="light") => {
  const data=[["OPEN DECISIONS","03","1 expiring"],["CONTROL POSTURE","97.4%","+0.6%"],["MONTHLY VARIANCE","+$42.8k","2.1%"],["LAST ATTESTATION","08:42","run 4218"]];
  let s="",xx=x; data.forEach((d,i)=>{
    const w=widths[i], dark=variant==="dark";
    s+=r(xx,y,w,104,dark?"#351420":P.white,dark?"#5D3443":P.line,4);
    s+=t(xx+18,y+27,d[0],10,700,dark?"#C9B9BF":P.grey,"start",.8);
    s+=t(xx+18,y+69,d[1],27,620,dark?P.white:P.ink)+t(xx+w-18,y+69,d[2],11,500,dark?"#C9B9BF":P.grey,"end");
    xx+=w+14;
  }); return s;
};

// 21 — Editorial Burgundy: warm paper, burgundy masthead, thin red brand rule.
let s=start(P.cream);
s+=r(0,0,1600,126,P.maroon)+r(0,126,1600,5,P.red);
s+=t(42,45,"DBX / PLATFORM",15,700,P.white,"start",1.2)+t(42,91,"MISSION CONTROL",11,600,"#CBBEC2","start",1);
s+=t(1548,53,"PRODUCTION",11,700,P.white,"end",.8)+t(1548,84,"Updated 08:42 EDT",12,400,"#CBBEC2","end");
s+=t(54,182,"01  /  EXECUTIVE OPERATIONS",10,700,P.redDark,"start",1.1)+t(54,229,"Production workspace",38,600,P.maroon);
s+=t(54,260,"Decisions, evidence, cost, and control posture.",15,400,P.grey);
s+=metrics(54,302,[260,260,260,260]);
s+=commonTable(54,430,850,P.red);
s+=r(930,430,616,406,P.white,P.line,4)+t(956,465,"SELECTED DECISION",10,700,P.grey,"start",.8);
s+=t(956,512,"Reconcile production",25,600,P.maroon)+t(956,544,"SQL warehouse",25,600,P.maroon);
s+=tag(956,591,"Approval required")+tag(1122,591,"Expires 11:48",P.redDark,P.blush);
s+=l(956,622,1520,622)+t(956,658,"PLAN HASH",10,700,P.grey,"start",.7)+t(1090,658,"4f92c0bd…c18a",13,550,P.ink);
s+=t(956,697,"EXECUTOR",10,700,P.grey,"start",.7)+t(1090,697,"warehouse-executor",13,550,P.ink);
s+=t(956,736,"IMPACT",10,700,P.grey,"start",.7)+t(1090,736,"+$6.2k / month",13,550,P.ink);
s+=btn(956,773,"Review plan",true,142,P.redDark)+btn(1110,773,"Trace evidence",false,154);
s+=r(54,856,1492,104,P.blush,P.line,4)+t(80,888,"OPERATING NOTE",10,700,P.redDark,"start",.9);
s+=t(80,925,"No active incident · 18 of 18 evidence sources current · no unverified executions.",14,500,P.maroon);
s+=l(820,874,820,944)+t(852,895,"READ-ONLY BOUNDARY",10,700,P.grey,"start",.8)+t(852,928,"Assistant may explain evidence and draft proposals; execution requires approval.",13,500,P.ink);
fs.writeFileSync(path.join(out,"21-brand-editorial-burgundy.svg"),s+end);

// 22 — Redline Grid: white/black system with a precise red navigation rail.
s=start(P.white);
s+=r(0,0,214,1000,P.ink)+r(214,0,8,1000,P.red);
s+=t(28,42,"DBX",18,750,P.white)+t(28,69,"PLATFORM",10,650,"#A7A7A7","start",1.5)+navItems(14,136,"Overview");
s+=t(28,926,"PRODUCTION",10,700,"#929292","start",1)+c(30,954,4,P.green)+t(44,959,"Sources current",11,450,"#CACACA");
s+=r(222,0,1378,66,P.white,P.line)+t(258,40,"MISSION CONTROL / PRODUCTION",11,700,P.grey,"start",1)+t(1558,40,"SEARCH  ⌘K",11,600,P.grey,"end");
s+=t(258,116,"OPERATING OVERVIEW",10,700,P.red,"start",1.2)+t(258,164,"Workspace control plane",34,620,P.ink);
s+=metrics(258,206,[250,250,250,250]);
s+=commonTable(258,344,824,P.red);
s+=r(1104,344,454,278,P.pale,"none",0)+r(1104,344,5,278,P.red);
s+=t(1134,380,"DECISION IN FOCUS",10,700,P.grey,"start",.8)+t(1134,423,"Warehouse reconciliation",21,620,P.ink);
s+=tag(1134,466,"Human approval")+t(1134,514,"Immutable plan",11,500,P.grey)+t(1134,542,"4f92c0bd…c18a",15,600,P.ink);
s+=btn(1134,567,"Review exact plan",true,174,P.ink);
s+=r(258,652,1300,272,P.white,P.line,0)+t(284,688,"CONTROL SIGNALS",10,700,P.grey,"start",.9);
const sigs=[["EVIDENCE FRESHNESS","100%"],["FAILED CHECKS","0"],["POLICY COVERAGE","97.4%"],["UNVERIFIED ACTIONS","0"]];
sigs.forEach((d,i)=>{const x=284+i*306;s+=t(x,738,d[0],10,700,P.grey,"start",.7)+t(x,788,d[1],30,600,P.ink);if(i<3)s+=l(x+274,710,x+274,866,P.line);});
s+=l(284,826,1532,826)+t(284,864,"Semantic red is reserved for urgency, expiry, and the selected operating path.",12,450,P.grey);
fs.writeFileSync(path.join(out,"22-brand-redline-grid.svg"),s+end);

// 23 — Rose Ledger: pale rose atmosphere, maroon sidebar, financial-ledger density.
s=start(P.blush);
s+=r(0,0,246,1000,P.maroon)+r(0,0,246,8,P.red);
s+=t(28,47,"DBX / PLATFORM",14,700,P.white,"start",1.1)+t(28,78,"Institutional operations",11,430,"#CDBFC4");
s+=navItems(18,144,"Decisions");
s+=r(246,0,1354,66,P.white,P.line)+t(280,40,"MISSION CONTROL",11,700,P.maroon,"start",1.1)+t(1560,40,"PRODUCTION  ·  08:42 EDT",11,600,P.grey,"end");
s+=t(280,112,"DECISION LEDGER",10,700,P.redDark,"start",1.1)+t(280,158,"Review and approval queue",32,620,P.maroon);
s+=t(280,187,"Immutable plans ordered by expiry and operational consequence.",14,430,P.grey);
s+=r(280,222,1280,82,P.rose,"none",3)+t(306,254,"03",28,620,P.maroon)+t(306,279,"OPEN",9,700,P.grey,"start",.9);
s+=l(398,240,398,286,"#D8C3C9")+t(430,254,"01",28,620,P.redDark)+t(430,279,"EXPIRING",9,700,P.grey,"start",.9);
s+=l(548,240,548,286,"#D8C3C9")+t(580,254,"15 MIN",28,620,P.maroon)+t(580,279,"APPROVAL WINDOW",9,700,P.grey,"start",.9);
s+=t(1528,270,"Sort: expiry ↑",12,550,P.maroon,"end");
s+=r(280,328,1280,352,P.white,P.line,4)+t(304,360,"PRIORITY",10,700,P.grey,"start",.8)+t(400,360,"PLAN",10,700,P.grey,"start",.8)+t(982,360,"EVIDENCE",10,700,P.grey,"start",.8)+t(1226,360,"IMPACT",10,700,P.grey,"start",.8)+t(1508,360,"WINDOW",10,700,P.grey,"end",.8);
const ledger=[["P1","Reconcile production SQL warehouse","CURRENT","+$6.2k / mo","11:48"],["P2","Apply cluster policy delta","CURRENT","12 clusters","13:06"],["P3","Run manual security scan","4 SOURCES","read only","14:31"],["—","Cost allocation review","MODELLED","$38.4k est.","draft"]];
ledger.forEach((d,i)=>{const y=414+i*66;s+=t(304,y,d[0],12,700,i===0?P.redDark:P.ink)+t(400,y,d[1],14,600,P.ink)+tag(982,y,d[2],i===2?P.redDark:P.green,i===2?P.rose:"#EEF3EF")+t(1226,y,d[3],13,500,P.ink)+t(1508,y,d[4],13,600,P.ink,"end");if(i<3)s+=l(304,y+27,1536,y+27,P.line);});
s+=r(280,706,828,202,P.white,P.line,4)+t(306,741,"SELECTED PLAN",10,700,P.redDark,"start",.9)+t(306,780,"Reconcile production SQL warehouse",20,620,P.maroon);
s+=t(306,816,"Hash 4f92c0bd…c18a  ·  executor warehouse-executor",12,450,P.grey)+btn(306,848,"Open review",true,142,P.redDark)+btn(460,848,"View evidence",false,146);
s+=r(1132,706,428,202,P.maroon,"none",4)+t(1158,741,"EXECUTION BOUNDARY",10,700,"#CDBFC4","start",.9)+t(1158,780,"Approval is not execution.",20,600,P.white)+t(1158,817,"A separate confirmation and",13,430,"#DCCFD3")+t(1158,842,"revalidation are always required.",13,430,"#DCCFD3");
fs.writeFileSync(path.join(out,"23-brand-rose-ledger.svg"),s+end);

// 24 — Dark Maroon Command: immersive brand canvas, neutral data surfaces.
s=start(P.maroon);
s+=r(0,0,1600,7,P.red)+r(0,7,220,993,"#1A0B11");
s+=t(28,46,"DBX / PLATFORM",14,700,P.white,"start",1.1)+navItems(14,128,"Overview");
s+=t(28,926,"PRODUCTION",10,700,"#86727A","start",1)+c(30,954,4,"#7C9A82")+t(44,959,"Sources current",11,450,"#BBAEB3");
s+=t(254,45,"MISSION CONTROL",11,700,"#CDBFC4","start",1.1)+t(1556,45,"19 JUL 2026  ·  08:42 EDT",11,600,"#CDBFC4","end");
s+=t(254,108,"LIVE OPERATIONS",10,700,P.gold,"start",1.1)+t(254,154,"Production command",34,600,P.white)+t(254,184,"A focused view of decisions, evidence, and execution readiness.",14,430,"#CDBFC4");
s+=metrics(254,220,[250,250,250,250],"dark");
s+=commonTable(254,356,824,P.red,true);
s+=r(1100,356,456,278,"#351420","#5D3443",4)+t(1126,390,"OPERATING SIGNAL",10,700,"#CDBFC4","start",.9);
s+=t(1126,434,"No active incident",23,600,P.white)+tag(1126,475,"Stable",P.green,"#27382D");
s+=l(1126,506,1530,506,"#5D3443")+t(1126,542,"Evidence freshness",12,450,"#CDBFC4")+t(1530,542,"100%",13,650,P.white,"end");
s+=t(1126,581,"Failed checks",12,450,"#CDBFC4")+t(1530,581,"0",13,650,P.white,"end");
s+=r(254,664,1302,250,"#351420","#5D3443",4)+t(280,700,"SELECTED DECISION",10,700,"#CDBFC4","start",.9);
s+=t(280,742,"Reconcile production SQL warehouse",22,600,P.white)+tag(280,783,"Approval required",P.red,"#551827")+tag(450,783,"Expires 11:48",P.gold,"#46351E");
s+=t(280,832,"Immutable plan 4f92c0bd…c18a",13,450,"#D8CDD1")+btn(280,852,"Review exact plan",true,174,P.red);
s+=l(872,694,872,884,"#5D3443")+t(908,724,"CONTROL NOTE",10,700,"#CDBFC4","start",.9)+t(908,762,"Assistant is read-only.",17,600,P.white)+t(908,796,"Every mutation requires approval,",13,430,"#D8CDD1")+t(908,821,"confirmation, revalidation, and",13,430,"#D8CDD1")+t(908,846,"a dedicated executor.",13,430,"#D8CDD1");
fs.writeFileSync(path.join(out,"24-brand-dark-command.svg"),s+end);

// 25 — Brand Spine: dominant white canvas with a strong but disciplined maroon/red spine.
s=start(P.pale);
s+=r(0,0,1600,64,P.white)+r(0,0,14,1000,P.red)+t(42,39,"DBX / PLATFORM",14,700,P.maroon,"start",1.1);
s+=t(1558,39,"MISSION CONTROL  /  PRODUCTION",11,700,P.grey,"end",.9);
s+=r(14,64,304,936,P.maroon)+t(44,112,"CONTROL PLANE",10,700,"#CDBFC4","start",1.1)+t(44,157,"Workspace",30,550,P.white)+t(44,193,"overview",30,550,P.white);
s+=navItems(30,274,"Overview");
s+=l(44,618,286,618,"#563344")+t(44,656,"STATUS",10,700,"#A7939B","start",1)+c(46,691,5,"#8BA18F")+t(64,696,"Sources current",12,450,"#D8CDD1");
s+=t(44,932,"READ-ONLY",10,700,"#A7939B","start",1)+t(44,958,"Updated 08:42 EDT",11,430,"#CDBFC4");
s+=t(358,112,"PRODUCTION WORKSPACE",10,700,P.redDark,"start",1.1)+t(358,160,"Operational posture",34,620,P.maroon);
s+=metrics(358,202,[270,270,270,270]);
s+=commonTable(358,342,810,P.redDark);
s+=r(1190,342,368,278,P.white,P.line,4)+t(1216,378,"NEXT REVIEW",10,700,P.grey,"start",.9);
s+=t(1216,425,"11:48",42,600,P.maroon)+t(1216,456,"approval window remaining",12,450,P.grey);
s+=tag(1216,502,"Priority 1")+t(1216,550,"Warehouse reconciliation",15,600,P.ink)+btn(1216,570,"Review plan",true,146,P.redDark);
s+=r(358,650,1200,258,P.white,P.line,4)+t(384,686,"EVIDENCE AND CONTROL COVERAGE",10,700,P.grey,"start",.9);
const bars=[["Security",100],["Governance",97],["Cost",100],["Housekeeping",94]];
bars.forEach((d,i)=>{const y=738+i*39;s+=t(384,y,d[0],12,500,P.ink)+r(520,y-14,800,15,"#ECE8E9","none",1)+r(520,y-14,d[1]*8,15,i===3?P.redDark:P.maroon,"none",1)+t(1352,y,`${d[1]}%`,12,650,P.ink);});
s+=l(1392,714,1392,870,P.line)+t(1422,738,"18 / 18",24,620,P.maroon)+t(1422,766,"sources current",11,450,P.grey)+t(1422,818,"0",24,620,P.maroon)+t(1422,846,"unverified actions",11,450,P.grey);
fs.writeFileSync(path.join(out,"25-brand-spine.svg"),s+end);
