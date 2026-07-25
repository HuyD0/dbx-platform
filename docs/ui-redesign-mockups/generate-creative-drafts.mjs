import fs from "node:fs";
import path from "node:path";

const out = path.dirname(new URL(import.meta.url).pathname);
const C={red:"#E31B3D",crimson:"#A51D3D",maroon:"#2A0D18",wine:"#461426",rose:"#F3E5E9",
  blush:"#FBF6F7",cream:"#F4F0EC",gold:"#C5A064",ink:"#151515",grey:"#6E6B6C",
  line:"#D9D3D3",white:"#FFFFFF",green:"#4B6F52",blue:"#496878"};
const esc=s=>String(s).replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;");
const r=(x,y,w,h,f,st="none",rx=0,sw=1)=>`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${rx}" fill="${f}" stroke="${st}" stroke-width="${sw}"/>`;
const t=(x,y,s,z=14,w=400,f=C.ink,a="start",ls=0)=>`<text x="${x}" y="${y}" font-size="${z}" font-weight="${w}" fill="${f}" text-anchor="${a}" letter-spacing="${ls}">${esc(s)}</text>`;
const l=(x1,y1,x2,y2,st=C.line,sw=1,ds="")=>`<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${st}" stroke-width="${sw}"${ds?` stroke-dasharray="${ds}"`:""}/>`;
const c=(x,y,rad,f,st="none",sw=1)=>`<circle cx="${x}" cy="${y}" r="${rad}" fill="${f}" stroke="${st}" stroke-width="${sw}"/>`;
const p=(d,f="none",st=C.ink,sw=1)=>`<path d="${d}" fill="${f}" stroke="${st}" stroke-width="${sw}"/>`;
const svg=bg=>`<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="1000" viewBox="0 0 1600 1000"><style>text{font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;font-variant-numeric:tabular-nums}</style>${r(0,0,1600,1000,bg)}`;
const end="</svg>";
const pill=(x,y,label,fg=C.crimson,bg=C.rose)=>{
  const w=label.length*7+28;return r(x,y-20,w,29,bg,"none",2)+c(x+12,y-5,3,fg)+t(x+22,y,label.toUpperCase(),10,700,fg,"start",.7);
};
const btn=(x,y,label,w=150,brand=C.maroon)=>r(x,y,w,40,brand,brand,3)+t(x+w/2,y+26,label,12,700,C.white,"middle");
const brand=tone=>t(40,43,"DBX / PLATFORM",14,750,tone,"start",1.2)+t(1560,43,"MISSION CONTROL  ·  PRODUCTION",11,650,tone,"end",.9);
const smallRow=(x,y,label,value,tone=C.ink)=>{
  return t(x,y,label.toUpperCase(),10,700,C.grey,"start",.7)+t(x,y+35,value,20,620,tone);
};

// 26 — Decision Ribbon: an approval journey becomes the organizing architecture.
let s=svg(C.blush)+r(0,0,1600,66,C.white)+r(0,0,12,1000,C.red)+brand(C.maroon);
s+=t(62,120,"DECISION JOURNEY",10,750,C.crimson,"start",1.2)+t(62,169,"One plan. Six controlled states.",36,620,C.maroon);
s+=t(62,201,"The interface follows the decision instead of organizing work into dashboard cards.",14,430,C.grey);
s+=p("M 90 294 C 300 294, 300 382, 510 382 S 720 470, 930 470 S 1140 558, 1510 558","none",C.maroon,34);
s+=p("M 90 294 C 300 294, 300 382, 510 382 S 720 470, 930 470","none",C.red,8);
const stages=[[92,294,"01","EVIDENCE","18 / 18 current"],[340,338,"02","PLAN","4f92…c18a"],[590,426,"03","APPROVAL","11:48 left"],[838,426,"04","CONFIRM","separate step"],[1088,514,"05","EXECUTE","dedicated SP"],[1372,558,"06","VERIFY","append only"]];
stages.forEach((d,i)=>{s+=c(d[0],d[1],29,i<3?C.red:C.white,i<3?C.red:C.maroon,3)+t(d[0],d[1]+5,d[2],12,750,i<3?C.white:C.maroon,"middle");
  const yy=i===5?d[1]-76:(i%2===0?d[1]-70:d[1]+75);s+=t(d[0],yy,d[3],10,750,C.crimson,"middle",.9)+t(d[0],yy+25,d[4],13,600,C.maroon,"middle");});
s+=r(62,654,918,264,C.white,C.line,5)+t(88,690,"CURRENT STATE  /  HUMAN APPROVAL",10,750,C.crimson,"start",1);
s+=t(88,736,"Reconcile production SQL warehouse",25,620,C.maroon)+pill(88,776,"Expires 11:48")+pill(246,776,"Evidence current",C.green,"#EDF2ED");
s+=t(88,824,"Impact",11,600,C.grey)+t(170,824,"+$6.2k / month",14,650,C.ink)+t(370,824,"Executor",11,600,C.grey)+t(460,824,"warehouse-executor",14,650,C.ink);
s+=btn(88,854,"Review exact plan",176,C.crimson)+r(276,854,148,40,C.white,C.line,3)+t(350,880,"Trace evidence",12,650,C.ink,"middle");
s+=r(1004,654,534,264,C.maroon,"none",5)+t(1034,691,"CONTROL PRINCIPLE",10,750,"#CDBFC4","start",1);
s+=t(1034,741,"Approval is a state,",23,580,C.white)+t(1034,773,"not an execution event.",23,580,C.white);
s+=l(1034,805,1508,805,"#684151")+t(1034,841,"Every transition is exact, expiring,",13,430,"#D8CDD1")+t(1034,867,"revalidated, and append-only.",13,430,"#D8CDD1");
fs.writeFileSync(path.join(out,"26-creative-decision-ribbon.svg"),s+end);

// 27 — Evidence Constellation: evidence topology surrounds a decision nucleus.
s=svg(C.maroon)+r(0,0,1600,7,C.red)+brand(C.white);
s+=t(48,113,"EVIDENCE TOPOLOGY",10,750,C.gold,"start",1.2)+t(48,160,"Decision constellation",34,600,C.white);
s+=t(48,190,"A spatial view of provenance, freshness, and the plan they support.",14,430,"#CDBFC4");
s+=c(850,520,224,"#35131F","#6B3E4F",1)+c(850,520,158,"#401626","#754556",1)+c(850,520,94,C.white);
s+=t(850,494,"PLAN",10,750,C.crimson,"middle",1)+t(850,526,"4f92…c18a",18,700,C.maroon,"middle")+t(850,555,"11:48 remaining",12,500,C.grey,"middle");
const nodes=[[420,330,"COST","usage / 4218","CURRENT"],[1245,300,"SECURITY","audit / 4217","CURRENT"],[1320,650,"POLICY","commit 8c21d6","VERIFIED"],[420,730,"IDENTITY","approvers","CURRENT"],[810,840,"AUDIT","append stream","HEALTHY"],[1060,790,"COMPUTE","clusters / 4216","98.6%"]];
nodes.forEach((d,i)=>{const x=d[0],y=d[1];s+=l(x,y,850,520,i===2?C.gold:"#74505D",1.5,i===5?"6 5":"");s+=c(x,y,42,i===2?C.crimson:"#35131F",i===2?C.red:"#8C6875",2);
  s+=t(x,y-60,d[2],10,750,i===2?C.gold:"#CDBFC4","middle",.9)+t(x,y+66,d[3],12,550,C.white,"middle")+t(x,y+88,d[4],10,650,i===5?C.gold:"#97B09C","middle",.6);});
s+=r(48,250,262,434,"#35131F","#5B3342",5)+t(74,286,"SOURCE REGISTER",10,750,"#CDBFC4","start",.9);
s+=t(74,334,"18 / 18",38,600,C.white)+t(74,365,"sources reporting",12,450,"#CDBFC4");
s+=l(74,398,284,398,"#604050")+smallRow(74,438,"Freshness","100%",C.white)+smallRow(74,508,"Coverage","97.4%",C.white)+smallRow(74,578,"Unknown scope","0",C.white);
s+=r(1178,742,360,176,C.white,"none",5)+t(1204,776,"SUPPORTED DECISION",10,750,C.grey,"start",.9)+t(1204,815,"Reconcile warehouse",20,620,C.maroon)+pill(1204,856,"Approval required")+btn(1204,872,"Open plan",128,C.crimson);
fs.writeFileSync(path.join(out,"27-creative-evidence-constellation.svg"),s+end);

// 28 — Annual-report Brief: editorial hierarchy and a dramatic brand number.
s=svg(C.cream)+r(0,0,1600,70,C.maroon)+brand(C.white)+r(0,70,1600,6,C.red);
s+=r(0,76,535,924,C.red)+t(52,134,"SUNDAY / 19 JULY 2026",11,750,C.white,"start",1.2);
s+=t(52,230,"03",148,520,C.white)+t(56,278,"DECISIONS REQUIRE REVIEW",11,750,C.white,"start",1.1);
s+=l(54,322,480,322,"#FFFFFF80")+t(54,374,"One approval expires",27,550,C.white)+t(54,410,"before 09:00.",27,550,C.white);
s+=t(54,494,"11:48",55,580,C.white)+t(54,528,"REMAINING",10,750,C.white,"start",1);
s+=r(54,584,426,214,C.maroon,"none",3)+t(80,622,"PRIORITY PLAN",10,750,"#D6C6CC","start",1)+t(80,668,"Reconcile production",22,600,C.white)+t(80,697,"SQL warehouse",22,600,C.white);
s+=t(80,744,"Impact  +$6.2k / month",13,500,"#D6C6CC")+btn(80,764,"Review plan",144,C.white)+t(152,790,"Review plan",12,700,C.maroon,"middle");
s+=t(590,142,"MORNING BRIEF",10,750,C.crimson,"start",1.2)+t(590,197,"The workspace is stable.",42,580,C.maroon);
s+=t(590,234,"Evidence is current. Control coverage improved. Compute remains the cost driver.",15,430,C.grey);
s+=l(590,282,1538,282)+t(590,330,"CONTROL POSTURE",10,750,C.grey,"start",1)+t(590,395,"97.4%",62,560,C.maroon)+t(836,392,"+0.6%",16,650,C.green);
s+=t(590,438,"Four documented exceptions",13,500,C.ink)+t(590,465,"No unknown scope",13,500,C.grey);
s+=l(960,314,960,486)+t(1010,330,"MONTH TO DATE",10,750,C.grey,"start",1)+t(1010,395,"$2.08m",62,560,C.maroon)+t(1280,392,"+2.1%",16,650,C.crimson);
s+=t(1010,438,"Forecast remains within budget",13,500,C.ink)+t(1010,465,"Compute variance +8.2%",13,500,C.grey);
s+=l(590,526,1538,526)+t(590,570,"OVERNIGHT / EXACT RUNS",10,750,C.crimson,"start",1);
const runs=[["08:40","Cost evidence attested","job 4218"],["08:38","Security posture attested","job 4217"],["08:37","Policy source verified","commit 8c21d6"],["07:15","Budget forecast refreshed","job 4211"]];
runs.forEach((d,i)=>{const y=620+i*60;s+=t(590,y,d[0],13,700,C.maroon)+t(684,y,d[1],14,550,C.ink)+t(1518,y,d[2],12,500,C.grey,"end");if(i<3)s+=l(590,y+25,1538,y+25,C.line);});
s+=r(590,870,948,70,C.blush,C.line,3)+t(612,899,"READ-ONLY ASSISTANT",10,750,C.crimson,"start",.9)+t(612,924,"May cite evidence and draft proposals. It cannot execute a target mutation.",13,500,C.ink);
fs.writeFileSync(path.join(out,"28-creative-annual-brief.svg"),s+end);

// 29 — Twin Lens: executive narrative and operator evidence share one decisive seam.
s=svg(C.white)+r(0,0,1600,8,C.red)+brand(C.maroon);
s+=r(0,66,785,934,C.blush)+r(785,66,815,934,C.maroon)+r(775,66,10,934,C.red);
s+=t(52,125,"EXECUTIVE LENS",10,750,C.crimson,"start",1.2)+t(52,177,"Why this decision",36,600,C.maroon)+t(52,211,"Business consequence, control posture, and timing.",14,430,C.grey);
s+=t(52,292,"+$6.2k",66,560,C.maroon)+t(286,288,"per month",18,500,C.grey);
s+=t(52,332,"Estimated cost of starting the production warehouse.",13,500,C.ink);
s+=l(52,380,724,380)+smallRow(52,426,"Control posture","97.4%",C.maroon)+smallRow(282,426,"Evidence","18 / 18",C.maroon)+smallRow(512,426,"Expiry","11:48",C.crimson);
s+=r(52,526,672,274,C.white,C.line,4)+t(78,562,"DECISION MEMO",10,750,C.grey,"start",.9);
s+=t(78,605,"Reconcile production SQL warehouse",21,620,C.maroon);
s+=t(78,644,"The warehouse is stopped. Starting it restores dashboard",14,430,C.ink)+t(78,670,"availability with a 10-minute auto-stop control.",14,430,C.ink);
s+=t(78,716,"Recommendation",11,650,C.grey)+t(190,716,"Approve after exact plan review",14,650,C.crimson)+btn(78,742,"Review plan",150,C.crimson);
s+=t(830,125,"OPERATOR LENS",10,750,C.gold,"start",1.2)+t(830,177,"What will happen",36,600,C.white)+t(830,211,"Exact payload, identity, and fail-closed boundary.",14,430,"#CDBFC4");
s+=r(830,258,718,150,"#35131F","#684151",4)+t(856,292,"IMMUTABLE PLAN",10,750,"#CDBFC4","start",.9)+t(856,336,"4f92c0bd…c18a",24,650,C.white);
s+=pill(856,378,"Approval required",C.red,"#551827")+pill(1022,378,"Evidence current",C.green,"#283A2D");
const exact=[["TARGET","warehouse / dbx-platform-prod"],["STATE","RUNNING · auto-stop 10 min"],["EXECUTOR","warehouse-executor"],["CREATED","08:29:54 EDT"]];
exact.forEach((d,i)=>{const y=464+i*62;s+=t(830,y,d[0],10,750,"#A9939B","start",.8)+t(1012,y,d[1],14,550,C.white);s+=l(830,y+24,1548,y+24,"#5B3544");});
s+=r(830,742,718,150,"#35131F","#684151",4)+t(856,776,"FAIL-CLOSED",10,750,C.gold,"start",.9)+t(856,812,"Hash mismatch · drift · expiry · replay",14,550,C.white)+t(856,842,"Missing identity or audit storage",14,550,C.white)+t(856,872,"Approval never executes the plan.",12,500,"#CDBFC4");
s+=t(52,936,"TWO LENSES  /  ONE EXACT DECISION",10,750,C.crimson,"start",1.1);
fs.writeFileSync(path.join(out,"29-creative-twin-lens.svg"),s+end);

// 30 — Command Monolith: a strong central object carries the decision through the page.
s=svg(C.pale)+r(0,0,1600,66,C.white)+brand(C.maroon)+r(64,108,1472,824,C.white,C.line,5);
s+=r(640,108,320,824,C.maroon)+r(640,108,8,824,C.red);
s+=t(96,153,"WORKSPACE SIGNAL",10,750,C.crimson,"start",1.1)+t(96,201,"Stable",38,600,C.maroon)+pill(96,246,"18 sources current",C.green,"#EDF2ED");
s+=l(96,286,586,286)+smallRow(96,333,"Control posture","97.4%",C.maroon)+smallRow(330,333,"Failed checks","0",C.maroon);
s+=t(96,452,"COST SIGNAL",10,750,C.crimson,"start",1.1)+t(96,510,"+$42.8k",44,580,C.maroon)+t(96,544,"Month-to-date variance",13,450,C.grey);
s+=l(96,586,586,586)+t(96,630,"Compute is the primary driver.",14,550,C.ink)+t(96,658,"Forecast remains within budget.",14,450,C.grey);
s+=t(680,153,"DECISION",10,750,"#CDBFC4","start",1.1)+t(680,214,"01",54,560,C.white)+t(680,257,"PRIORITY",10,750,C.gold,"start",1);
s+=t(680,330,"Reconcile",27,600,C.white)+t(680,365,"production SQL",27,600,C.white)+t(680,400,"warehouse",27,600,C.white);
s+=pill(680,455,"Approval required",C.red,"#551827")+pill(680,499,"Expires 11:48",C.gold,"#44321F");
s+=l(680,540,920,540,"#684151")+t(680,580,"PLAN",10,750,"#A9939B","start",.8)+t(680,612,"4f92…c18a",18,650,C.white);
s+=t(680,660,"IMPACT",10,750,"#A9939B","start",.8)+t(680,692,"+$6.2k / mo",18,650,C.white);
s+=btn(680,752,"Review exact plan",190,C.red)+t(680,824,"Approval ≠ execution",13,600,C.gold);
s+=t(1000,153,"CONTROL SEQUENCE",10,750,C.crimson,"start",1.1);
const seq=[["01","Evidence","current"],["02","Plan","immutable"],["03","Approval","pending"],["04","Confirmation","required"],["05","Execution","dedicated"],["06","Verification","append-only"]];
seq.forEach((d,i)=>{const y=206+i*100;s+=c(1020,y,19,i<2?C.maroon:C.white,C.maroon,2)+t(1020,y+4,d[0],9,750,i<2?C.white:C.maroon,"middle");
  if(i<5)s+=l(1020,y+20,1020,y+80,C.line,2);s+=t(1062,y-2,d[1],14,650,C.ink)+t(1062,y+24,d[2],12,450,i===2?C.crimson:C.grey);});
s+=r(1000,820,484,74,C.blush,C.line,3)+t(1022,850,"EXECUTION BOUNDARY",10,750,C.crimson,"start",.9)+t(1022,876,"Separate confirmation and revalidation required.",13,550,C.ink);
fs.writeFileSync(path.join(out,"30-creative-command-monolith.svg"),s+end);
