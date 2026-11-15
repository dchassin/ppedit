"""PSS/E RAW case reader

`parse_raw(text)` converts a Siemens PTI PSS/E power flow case (`.raw`,
revisions 23 through 35) into a PYPOWER `ppc` dict of plain lists, plus
notes about the data that was not converted. `ppedit.Case.load` calls it
for `.raw` files; PSS/E is an input format only.

The conversion follows MATPOWER's `psse2mpc` (`psse_parse`,
`psse_convert`, `psse_convert_xfmr`, `psse_convert_hvdc`; MATPOWER is
BSD-3-Clause licensed, https://matpower.org), with these additions:

  - revisions 34 and 35 (system-wide data, 12 branch ratings and names,
    generator `NREG`, at the end of the record in 34 and after `IREG` in
    35, switched shunt `ID` in 35);
  - `@!` field name lines, when a section has one, locate the fields of
    single-line records (buses, loads, shunts, generators, branches,
    switching devices) by name, so writers that order fields differently
    are read correctly; without them the fields are taken by position;
  - a generator that regulates another bus (`IREG`), which PYPOWER
    cannot do, gets its own bus voltage in the case as `Vg` instead of
    `VS`, so a solved case solves to the same voltages (a note says how
    many); a note also counts generators held at a reactive power limit,
    since PYPOWER power flow holds `Vg` there instead;
  - out-of-service switched shunts (revision 32 and later) and fixed
    shunts and loads are left out;
  - closed in-service system switching devices (revision 34 and later)
    become branches with their reactance `X` (default 0.0001 pu);
  - a winding nominal voltage `NOMV` of 0 means the bus base voltage;
  - 3-winding transformer star buses get the base voltage of the winding 1
    bus (MATPOWER uses 1 kV).

What the PYPOWER format cannot hold is reported in the notes and dropped:
bus, branch and machine names, owners, transformer magnetizing admittance
and impedance correction, remote voltage regulation, switched shunt
blocks (the initial susceptance `BINIT` is kept), VSC and multi-terminal
DC lines, FACTS devices, multi-section line groupings, zones and owners
tables, inter-area transfers, GNE devices, induction machines and
substation data. Constant current and constant admittance loads become
constant power loads at the bus voltage in the case (as in `psse2mpc`).
PSS/E has no cost data, so the case has no `gencost` rows.
"""

import math
import re

_EOS = re.compile(r"^\s*(Q|0)\s*(/.*)?$")
_LABEL = re.compile(r"END\s+OF\s+(.*?)\s+DATA",re.I)
_REV = re.compile(r"PSS(?:/|\(tm\))E-(\d+)",re.I)
_STAR_VMAX,_STAR_VMIN = 1.1,0.9

class RawError(ValueError):
    """Raised for an unreadable PSS/E RAW file"""

def _fields(line):
    """Split a RAW record into fields (comma or blank separated, quoted text
    kept as one field, `/` starts a comment); an empty field between two
    commas is `""` (the default value)"""
    out = []
    started = False
    i,n = 0,len(line)
    while i < n:
        c = line[i]
        if c in " \t\r":
            i += 1
        elif c == ",":
            if not started:
                out.append("")
            started = False
            i += 1
        elif c == "/":
            break
        elif c in "'\"":
            j = i + 1
            buf = []
            while j < n:
                if line[j] == c:
                    if j + 1 < n and line[j+1] == c:
                        buf.append(c)
                        j += 2
                        continue
                    break
                buf.append(line[j])
                j += 1
            out.append("".join(buf))
            started = True
            i = j + 1
        else:
            j = i
            while j < n and line[j] not in " \t\r,/'\"":
                j += 1
            out.append(line[i:j])
            started = True
            i = j
    return out

class _Rec(list):
    """One RAW record: fields addressed by 1-based column number"""

    def num(self,k,default=0.0):
        """Column `k` as a float (`default` if missing or empty)"""
        if k > len(self) or self[k-1].strip() == "":
            return default
        text = self[k-1].strip().replace("D","E").replace("d","e")
        try:
            return float(text)
        except ValueError as err:
            raise RawError(f"'{self[k-1]}' is not a number "
                f"(column {k} of '{','.join(self)}')") from err

    def int(self,k,default=0):
        """Column `k` as an int"""
        return int(round(self.num(k,default)))

def _field_names(line):
    """Field names of an `@!` header line, upper case, without quotes or blanks"""
    return [re.sub(r"[\s'\"]","",x).upper() for x in line.lstrip()[2:].split(",")]

def _read(text):
    """Return the header lines, the sections (lists of records), their
    labels, and the field names of each section's first `@!` line (`None`
    if it has none), as `psse_read` splits them"""
    lines = re.split(r"\r\n|\n|\r",text)
    header = []
    while lines and len(header) < 3:
        line = lines.pop(0)
        if not line.lstrip().startswith("@!"):
            header.append(line)
    if len(header) < 3:
        raise RawError("too few lines for a PSS/E RAW file")
    sections,labels,fields = [],[],[]
    current,names = [],None
    for line in lines:
        if line.lstrip().startswith("@!"):
            if not current and names is None:
                names = _field_names(line)
        elif _EOS.match(line):
            sections.append(current)
            fields.append(names)
            match = _LABEL.search(line)
            labels.append(match.group(1).upper() if match else "")
            current,names = [],None
            if line.lstrip().startswith("Q"):
                break
        elif line.strip():
            current.append(line)
    else:
        if current:
            sections.append(current)
            fields.append(names)
            labels.append("")
    return header,sections,labels,fields

def _order(rev,system_wide):
    """Section names in file order for revision `rev`"""
    out = []
    if system_wide:
        out.append("system-wide")
    out.append("bus")
    if rev >= 24:
        out.append("load")
    if rev > 30:
        out.append("fixed shunt")
    out += ["generator","branch"]
    if rev >= 34:
        out.append("system switching device")
    out.append("transformer adjustment" if rev <= 27 else "transformer")
    out += ["area","two-terminal dc"]
    if rev > 28:
        out.append("voltage source converter")
    if rev < 31:
        out.append("switched shunt")
    out += ["impedance correction","multi-terminal dc","multi-section line","zone",
        "inter-area transfer"]
    if rev > 24:
        out.append("owner")
    if rev > 25:
        out.append("facts device")
    if rev > 30:
        out.append("switched shunt")
    if rev > 31:
        out.append("gne device")
    if rev > 32:
        out.append("induction machine")
    if rev >= 35:
        out.append("substation")
    return out

def parse_raw(text:str) -> tuple[dict,list[str]]:
    """Convert PSS/E RAW text to a PYPOWER case

    Arguments
    ---------

      - `text`: RAW file contents

    Returns
    -------

      - `dict`: `ppc` dict (`version`, `baseMVA`, `bus`, `gen`, `branch`,
        `dcline`; lists of rows in PYPOWER column order) and
        `psse_revision`, the PSS/E revision the file was read as

      - `list[str]`: notes on what was not converted

    Exceptions
    ----------

      - `RawError`: the text is not a readable RAW case
    """
    # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    header,sections,labels,fields = _read(text)
    head = _Rec(_fields(header[0]))
    notes = []
    sbase = head.num(2,0.0)
    if sbase <= 0:
        raise RawError("the first line has no valid system MVA base (SBASE); "
            "is this a PSS/E RAW file?")
    rev = head.int(3,0)
    if not rev:
        match = _REV.search(header[0])
        rev = int(match.group(1)) if match else 0
    if not rev:
        rev = 23
        notes.append(f"PSS/E revision not given; read as revision {rev}")
    if rev < 23:
        raise RawError(f"PSS/E revision {rev} is not supported (23 to 35)")
    if rev > 35:
        notes.append(f"PSS/E revision {rev} is newer than 35; read as revision 35")
        rev = 35
    if head.int(1,0) != 0:
        notes.append(f"IC = {head.int(1,0)}: this may be a change case, not a base case")
    system_wide = rev >= 34 or (bool(labels) and "SYSTEM" in labels[0])
    names = _order(rev,system_wide)
    data,heads = {},{}
    for k,records in enumerate(sections):
        if k >= len(names):
            if records:
                notes.append(f"{len(sections)-len(names)} section(s) after the "
                    f"{names[-1]} data were not read")
            break
        data.setdefault(names[k],[]).append(records)
        if fields[k] and names[k] not in heads:
            heads[names[k]] = fields[k]
    def columns(name,cols):
        """1-based columns of the fields `cols` (name: (default column, aliases...))
        in section `name`, from its `@!` field names when it has them"""
        head = heads.get(name) if rev >= 31 else None
        out = {}
        for key,(col,*aliases) in cols.items():
            found = [head.index(x)+1 for x in (key,*aliases) if head and x in head]
            out[key] = found[0] if found else col
        return out
    def sect(name):
        """Records of all sections called `name` (switched shunts may be in two places)"""
        return [r for block in data.get(name,[]) for r in block]
    def recs(name):
        return [_Rec(_fields(x)) for x in sect(name)]
    kept = {"system-wide","bus","load","fixed shunt","generator","branch",
        "system switching device","transformer","area","two-terminal dc","switched shunt"}
    skipped = [f"{len(sect(x))} {x}" for x in names if x not in kept and sect(x)]
    if skipped:
        notes.append("PSS/E data not converted (records): " + ", ".join(skipped))

    # ----- buses -----
    bus = []
    nolimits = 0
    bcols = columns("bus",{"BASKV":(3,),"IDE":(4,),"AREA":(5,),"ZONE":(6,),"VM":(8,),
        "VA":(9,),"NVHI":(10,),"NVLO":(11,)})
    for r in recs("bus"):
        if rev < 24:    # I,IDE,PL,QL,GL,BL,IA,VM,VA,'NAME',BASKV,ZONE
            row = [r.int(1),r.int(2,1),r.num(3),r.num(4),r.num(5),r.num(6),r.int(7,1),
                r.num(8,1.0),r.num(9),r.num(11),r.int(12,1),None,None]
        elif rev < 31:  # I,'NAME',BASKV,IDE,GL,BL,AREA,ZONE,VM,VA,OWNER
            row = [r.int(1),r.int(4,1),0.0,0.0,r.num(5),r.num(6),r.int(7,1),
                r.num(9,1.0),r.num(10),r.num(3),r.int(8,1),None,None]
        else:           # I,'NAME',BASKV,IDE,AREA,ZONE,OWNER,VM,VA,NVHI,NVLO,EVHI,EVLO
            c = bcols
            row = [r.int(1),r.int(c["IDE"],1),0.0,0.0,0.0,0.0,r.int(c["AREA"],1),
                r.num(c["VM"],1.0),r.num(c["VA"]),r.num(c["BASKV"]),r.int(c["ZONE"],1),
                r.num(c["NVHI"]) if len(r) >= c["NVHI"] and r[c["NVHI"]-1].strip() else None,
                r.num(c["NVLO"]) if len(r) >= c["NVLO"] and r[c["NVLO"]-1].strip() else None]
        if row[11] is None or row[12] is None:
            nolimits += 1
            row[11] = _STAR_VMAX if row[11] is None else row[11]
            row[12] = _STAR_VMIN if row[12] is None else row[12]
        if row[1] not in (1,2,3,4):
            row[1] = 4 if row[1] < 0 else 1
        bus.append(row)
    if not bus:
        raise RawError("the file has no bus data")
    if nolimits:
        notes.append(f"{nolimits} bus(es) have no voltage limits; "
            f"using Vmin = {_STAR_VMIN:g} pu, Vmax = {_STAR_VMAX:g} pu")
    bus.sort(key=lambda x:x[0])
    index = {}
    for n,row in enumerate(bus):
        if row[0] in index:
            raise RawError(f"bus {row[0]} is defined more than once")
        index[row[0]] = n
    def at(number,what):
        if number not in index:
            raise RawError(f"{what} refers to bus {number}, which is not defined")
        return bus[index[number]]

    # ----- loads (I,ID,STATUS,AREA,ZONE,PL,QL,IP,IQ,YP,YQ,...) -----
    c = columns("load",{"STAT":(3,"STATUS"),"PL":(6,),"QL":(7,),"IP":(8,),"IQ":(9,),
        "YP":(10,),"YQ":(11,)})
    for r in recs("load"):
        if r.int(c["STAT"],1) != 1:
            continue
        b = at(r.int(1),"a load")
        vm = b[7]
        b[2] += r.num(c["PL"]) + r.num(c["IP"])*vm + r.num(c["YP"])*vm**2
        b[3] += r.num(c["QL"]) + r.num(c["IQ"])*vm - r.num(c["YQ"])*vm**2

    # ----- fixed shunts (I,ID,STATUS,GL,BL) -----
    c = columns("fixed shunt",{"STATUS":(3,"STAT"),"GL":(4,),"BL":(5,)})
    for r in recs("fixed shunt"):
        if r.int(c["STATUS"],1) == 1:
            b = at(r.int(1),"a fixed shunt")
            b[4] += r.num(c["GL"])
            b[5] += r.num(c["BL"])

    # ----- switched shunts (BINIT only) -----
    if rev <= 27:
        binit,status = 6,None
    elif rev <= 29:
        binit,status = 7,None
    elif rev < 32:
        binit,status = 8,None
    elif rev < 35:      # I,MODSW,ADJM,STAT,VSWHI,VSWLO,SWREM,RMPCT,'RMIDNT',BINIT,...
        binit,status = 10,4
    else:               # I,'ID',MODSW,ADJM,SWSTAT,VSWHI,VSWLO,SWREG,NREG,RMPCT,'RMIDNT',BINIT
        binit,status = 12,5
    if status is not None:
        c = columns("switched shunt",{"BINIT":(binit,),"STAT":(status,"ST","SWSTAT")})
        binit,status = c["BINIT"],c["STAT"]
    for r in recs("switched shunt"):
        if status is None or r.int(status,1) == 1:
            at(r.int(1),"a switched shunt")[5] += r.num(binit)

    # ----- generators -----
    gen = []
    if rev < 35:    # I,ID,PG,QG,QT,QB,VS,IREG,MBASE,ZR,ZX,RT,XT,GTAP,STAT,RMPCT,PT,PB
        cols = (3,4,5,6,7,9,15,17,18)   # (revision 34 has NREG at the end)
    else:           # I,ID,PG,QG,QT,QB,VS,IREG,NREG,MBASE,...,STAT(16),RMPCT,PT,PB
        cols = (3,4,5,6,7,10,16,18,19)
    c = columns("generator",dict(zip(("PG","QG","QT","QB","VS","MBASE","STAT","PT","PB",
        "IREG"),((x,) for x in cols+(8,)))))
    cols,ireg = tuple(c.values())[:9],c["IREG"]
    remote = 0
    defaults = (0.0,0.0,9999.0,-9999.0,1.0,sbase,1,9999.0,-9999.0)
    for r in recs("generator"):
        at(r.int(1),"a generator")
        v = [r.num(c,d) for c,d in zip(cols,defaults)]
        if r.int(ireg,0) not in (0,r.int(1)):
            v[4] = bus[index[r.int(1)]][7]  # PYPOWER regulates only the generator bus
            remote += 1
        gen.append([r.int(1),v[0],v[1],v[2],v[3],v[4],v[5],1 if v[6] > 0 else 0,v[7],v[8]]
            + [0.0]*11)

    def at_limit(g):
        """In service at a Q limit, with Vg not the case voltage of its bus"""
        return g[7] and min(abs(g[2]-g[3]),abs(g[2]-g[4])) < 1e-3 \
            and abs(g[5]-bus[index[g[0]]][7]) > 1e-4
    atlimit = sum(1 for g in gen if at_limit(g))
    if atlimit:
        notes.append(f"{atlimit} generator(s) are at a reactive power limit in the case; "
            "PYPOWER power flow holds their Vg instead of the limit, so voltages may differ")
    if remote:
        notes.append(f"{remote} generator(s) regulate the voltage of another bus (IREG), "
            "which PYPOWER cannot do; their Vg is the voltage of their own bus in the case")

    # ----- branches -----
    branch = []
    def shunt(number,g,b,on):
        if on and number in index:
            bus[index[number]][4] += g*sbase
            bus[index[number]][5] += b*sbase
    if rev >= 34:   # I,J,CKT,R,X,B,'NAME',RATE1..RATE12,GI,BI,GJ,BJ,ST
        c = columns("branch",{"R":(4,),"X":(5,),"B":(6,),"RATE1":(8,"RATEA"),
            "GI":(20,),"STAT":(24,"ST")})
    elif rev >= 31:
        c = columns("branch",{"R":(4,),"X":(5,),"B":(6,),"RATE1":(7,"RATEA"),
            "GI":(10,),"STAT":(14,"ST")})
    else:
        c = {"R":4,"X":5,"B":6}
    for r in recs("branch"):
        fbus,tbus = r.int(1),abs(r.int(2))
        at(fbus,"a branch")
        at(tbus,"a branch")
        if rev <= 27:   # I,J,CKT,R,X,B,RATEA,RATEB,RATEC,RATIO,ANGLE,GI,BI,GJ,BJ,ST
            rates,gi,st = 7,12,16
            ratio,angle = r.num(10),r.num(11)
        elif rev < 31:  # I,J,CKT,R,X,B,RATEA,RATEB,RATEC,GI,BI,GJ,BJ,ST
            rates,gi,st = 7,10,14
            ratio,angle = 0.0,0.0
        else:
            rates,gi,st = c["RATE1"],c["GI"],c["STAT"]
            ratio,angle = 0.0,0.0
        on = 1 if r.int(st,1) else 0
        branch.append([fbus,tbus,r.num(c["R"]),r.num(c["X"]),r.num(c["B"]),r.num(rates),
            r.num(rates+1),r.num(rates+2),ratio,angle,on,-360.0,360.0])
        shunt(fbus,r.num(gi),r.num(gi+1),on)
        shunt(tbus,r.num(gi+2),r.num(gi+3),on)

    # ----- system switching devices (I,J,CKT,X,RATE1..RATE12,STAT,NSTAT,...) -----
    switches = 0
    c = columns("system switching device",{"X":(4,),"RATE1":(5,),"STAT":(17,)})
    for r in recs("system switching device"):
        fbus,tbus = r.int(1),abs(r.int(2))
        at(fbus,"a switching device")
        at(tbus,"a switching device")
        on = 1 if r.int(c["STAT"],1) == 1 else 0
        rate = c["RATE1"]
        branch.append([fbus,tbus,0.0,r.num(c["X"],0.0001) or 0.0001,0.0,r.num(rate),
            r.num(rate+1),r.num(rate+2),0.0,0.0,on,-360.0,360.0])
        switches += 1
    if switches:
        notes.append(f"{switches} system switching device(s) converted to branches "
            "with their reactance X")

    # ----- transformers -----
    if rev > 27:
        _transformers(sect("transformer"),bus,index,branch,sbase,notes)

    # ----- two-terminal DC lines -----
    dcline = _dclines(sect("two-terminal dc"),bus,index,notes)

    data = {"version":"2","baseMVA":sbase,"bus":bus,"gen":gen,"branch":branch,
        "psse_revision":rev}
    if dcline:
        data["dcline"] = dcline
    return data,notes

def _transformers(lines,bus,index,branch,sbase,notes):
    """Convert 2- and 3-winding transformers (`psse_convert_xfmr`), appending
    branch rows and star buses"""
    # pylint: disable=too-many-locals,too-many-arguments,too-many-positional-arguments
    # pylint: disable=too-many-branches,too-many-statements
    two,three = [],[]
    i = 0
    while i < len(lines):
        first = _Rec(_fields(lines[i]))
        if first.int(3,0) == 0:
            two.append([first] + [_Rec(_fields(x)) for x in lines[i+1:i+4]])
            i += 4
        else:
            three.append([first] + [_Rec(_fields(x)) for x in lines[i+1:i+5]])
            i += 5
    if any(len(x) < 4 for x in two) or any(len(x) < 5 for x in three):
        raise RawError("transformer data ends in the middle of a transformer record")
    def basekv(number):
        return bus[index[number]][9]
    def zsys(r,x,cz,s,nomv,number): # pylint: disable=too-many-arguments,too-many-positional-arguments
        """Winding impedance in pu on the system base"""
        if cz == 3:     # R: load loss (W), X: |Z| (pu on the winding base)
            r = 1e-6*r/s
            x = math.sqrt(max(x*x-r*r,0.0))
        if cz in (2,3):
            kv = nomv or basekv(number)
            zb = kv*kv/s
            zbs = basekv(number)**2/sbase if basekv(number) else 0.0
            if zbs:
                r,x = r*zb/zbs,x*zb/zbs
            else:
                r,x = r*sbase/s,x*sbase/s
        return r,x
    def ratio(windv,nomv,cw,number):
        """Winding voltage in pu of the bus base voltage"""
        if cw == 1:
            return windv
        kv = basekv(number)
        if cw == 3:
            windv *= nomv or kv
        return windv/kv if kv else windv
    mag = 0
    for r1,r2,r3,r4 in two:
        fbus,tbus = r1.int(1),r1.int(2)
        if fbus not in index or tbus not in index:
            notes.append(f"transformer {fbus}-{tbus} refers to an undefined bus; not converted")
            continue
        cw,cz = r1.int(5,1),r1.int(6,1)
        if r1.num(8) or r1.num(9):
            mag += 1
        s = r2.num(3,sbase) or sbase
        r,x = zsys(r2.num(1),r2.num(2),cz,s,r3.num(2),fbus)
        tap = ratio(r3.num(1,1.0),r3.num(2),cw,fbus)/(ratio(r4.num(1,1.0),r4.num(2),cw,tbus) or 1.0)
        branch.append([fbus,tbus,r,x,0.0,r3.num(4),r3.num(5),r3.num(6),tap,r3.num(3),
            1 if r1.int(12,1) else 0,-360.0,360.0])
    base = 10**math.ceil(math.log10(max(row[0] for row in bus)+1))
    for n,(r1,r2,r3,r4,r5) in enumerate(three):
        ends = [r1.int(1),r1.int(2),r1.int(3)]
        if any(x not in index for x in ends):
            notes.append(f"3-winding transformer {'-'.join(map(str,ends))} refers to an "
                "undefined bus; not converted")
            continue
        cw,cz = r1.int(5,1),r1.int(6,1)
        if r1.num(8) or r1.num(9):
            mag += 1
        stat = r1.int(12,1)
        star = base + n + 1
        w1 = bus[index[ends[0]]]
        bus.append([star,1 if stat else 4,0.0,0.0,0.0,0.0,w1[6],r2.num(10,1.0),
            r2.num(11),w1[9],w1[10],_STAR_VMAX,_STAR_VMIN])
        index[star] = len(bus) - 1
        windings = (r3,r4,r5)
        sb = [r2.num(3,sbase) or sbase,r2.num(6,sbase) or sbase,r2.num(9,sbase) or sbase]
        z12 = zsys(r2.num(1),r2.num(2),cz,sb[0],r3.num(2),ends[0])
        z23 = zsys(r2.num(4),r2.num(5),cz,sb[1],r4.num(2),ends[1])
        z31 = zsys(r2.num(7),r2.num(8),cz,sb[2],r5.num(2),ends[2])
        z = [[(a+c-b)/2 for a,b,c in zip(z12,z23,z31)],
             [(a+b-c)/2 for a,b,c in zip(z12,z23,z31)],
             [(c+b-a)/2 for a,b,c in zip(z12,z23,z31)]]
        off = {0:(1,2,3),2:(2,),3:(3,),4:(1,)}.get(stat,())
        for k,(end,w) in enumerate(zip(ends,windings)):
            branch.append([end,star,z[k][0],z[k][1],0.0,w.num(4),w.num(5),w.num(6),
                ratio(w.num(1,1.0),w.num(2),cw,end),w.num(3),0 if k+1 in off else 1,
                -360.0,360.0])
    if three:
        stars = f"{base+1}" if len(three) == 1 else f"{base+1}-{base+len(three)}"
        notes.append(f"added bus(es) {stars} as star points of 3-winding transformers")
    if mag:
        notes.append(f"{mag} transformer(s) have magnetizing admittance, which is not converted")

def _qlims(p,amax,amin):
    """Converter reactive power limits for power `p` and firing angle limits (deg)"""
    qmin = abs(p*math.tan(math.radians(amin)))
    phi = math.degrees(math.acos(0.5*(math.cos(math.radians(amax))+0.5)))
    return qmin,abs(p*math.tan(math.radians(phi)))

def _dclines(lines,bus,index,notes):
    """Convert two-terminal DC lines (`psse_convert_hvdc`)"""
    # pylint: disable=too-many-locals
    out = []
    if len(lines) % 3:
        raise RawError("two-terminal DC data ends in the middle of a DC line record")
    for k in range(0,len(lines),3):
        r1,rr,ri = (_Rec(_fields(x)) for x in lines[k:k+3])
        mdc,setvl,vschd = r1.int(2,0),abs(r1.num(4)),r1.num(5)
        fbus,tbus = rr.int(1),ri.int(1)
        if fbus not in index or tbus not in index:
            notes.append(f"DC line {fbus}-{tbus} refers to an undefined bus; not converted")
            continue
        p = setvl if mdc == 1 else setvl*vschd/1000 if mdc == 2 else 0.0
        qrmin,qrmax = _qlims(p,rr.num(3),rr.num(4))
        qimin,qimax = _qlims(p,ri.num(3),ri.num(4))
        out.append([fbus,tbus,0 if mdc == 0 else 1,p,p,0.0,0.0,
            bus[index[fbus]][7],bus[index[tbus]][7],0.85*p,1.15*p,
            qrmin,qrmax,qimin,qimax,0.0,0.0])
    if out:
        notes.append(f"{len(out)} two-terminal DC line(s) converted with Pmin/Pmax at "
            "85%/115% of the scheduled power and no losses")
    return out
