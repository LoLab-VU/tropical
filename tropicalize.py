import networkx
import sympy
import re
import copy
import numpy
import sympy.parsing.sympy_parser
import itertools
import matplotlib.pyplot as plt
import pysb
import stoichiometry_analysis as sto
from pysb.integrate import odesolve
from collections import OrderedDict

def _Heaviside_num(x):
    return 0.5*(numpy.sign(x)+1)

def _parse_name(spec):
    m = spec.monomer_patterns
    lis_m = []
    for i in range(len(m)):
        tmp_1 = str(m[i]).partition('(')
        tmp_2 = re.findall(r"(?<=\').+(?=\')",str(m[i]))
        if tmp_2 == []: lis_m.append(tmp_1[0])
        else:
            lis_m.append(''.join([tmp_1[0],tmp_2[0]]))
    return '_'.join(lis_m)

class Tropical:
    def __init__(self, model):
        self.model              = model
        self.tspan              = None
        self.y                  = None  # ode solution, numpy array
        self.param_values       = None
        self.passengers         = None
        self.graph              = None
        self.sto_conserved      = None
        self.conservation       = None
        self.conserve_var       = None
        self.value_conservation = {}
        self.tro_species        = {}
        self.driver_signatures  = None
        self.passenger_signatures = None

    def __repr__(self):
        return "<%s '%s' (passengers: %s, cycles: %d) at 0x%x>" % \
            (self.__class__.__name__, self.model.name,
             self.passengers.__repr__(),
             len(self.cycles),
             id(self))

    def tropicalize(self,tspan=None, param_values=None, ignore=1, epsilon=1, rho=1, verbose=True):
        
        if verbose: print "Solving Simulation"
        
        if tspan is not None:
            self.tspan = tspan
        elif self.tspan is None:
            raise Exception("'tspan' must be defined.")
        
        if param_values is not None:
            # accept vector of parameter values as an argument
            if len(param_values) != len(self.model.parameters):
                raise Exception("param_values must be the same length as model.parameters")
            if not isinstance(param_values, numpy.ndarray):
                param_values = numpy.array(param_values)
        else:
            # create parameter vector from the values in the model
            param_values = numpy.array([p.value for p in self.model.parameters])

#         subs = dict((p, param_values[i]) for i, p in enumerate(self.model.parameters))
        new_pars = dict((p.name, param_values[i]) for i, p in enumerate(self.model.parameters))
        self.param_values = new_pars
              
        self.y = odesolve(self.model, self.tspan, self.param_values) 
          
        if verbose: print "Getting Passenger species"
        self.find_passengers(self.y[ignore:], verbose, epsilon)
        if verbose: print "Computing conservation relations"
        self.sto_conserved = sto.conservation_relations(self.model)
        if verbose: print "Computing Conservation laws"
        (self.conservation, self.conserve_var, self.value_conservation) = self.mass_conserved(self.y[ignore:])
        if verbose: print "Pruning Equations"
        self.pruned = self.pruned_equations(self.y[ignore:], rho)
        if verbose: print "Solving pruned equations"
        self.sol_pruned = self.solve_pruned()
        if verbose: print "equation to tropicalize"
        self.eqs_for_tropicalization = self.equations_to_tropicalize()
        if verbose: print "Getting tropicalized equations"
        self.tropical_eqs = self.final_tropicalization()
        self.data_drivers(self.y[ignore:])
        
        return 

    def find_passengers(self, y, verbose=False, epsilon=None, ptge_similar=0.9, plot=False):
        self.passengers = []
        solved_pol = []               # list of solved polynomial equations
        diff_eqs = []               #  list of differential equations   

        # Loop through all equations (i is equation number)
        for i, eq in enumerate(self.model.odes):
            eq        = eq.subs('__s%d' % i, '__s%dstar' % i)
            sol       = sympy.solve(eq, sympy.Symbol('__s%dstar' % i))        # Find equation of imposed trace
            for j in range(len(sol)):                                         # j is solution j for equation i (mostly likely never greater than 2)
                for p in self.param_values: sol[j] = sol[j].subs(p, self.param_values[p])    # Substitute parameters
                solved_pol.append(sol[j])
                diff_eqs.append(i)
        for k in range(len(solved_pol)):                                              # a is the list of solution of polinomial equations, b is the list of differential equations
            args = []                                                         #arguments to put in the lambdify function
            variables = [atom for atom in solved_pol[k].atoms(sympy.Symbol) if not re.match(r'\d',str(atom))]
            f = sympy.lambdify(variables, solved_pol[k], modules = dict(sqrt=numpy.lib.scimath.sqrt) )
            for l in variables:
                args.append(y[:][str(l)])
                
            hey = abs(numpy.log10(f(*args)) - numpy.log10(y['__s%d'%diff_eqs[k]]))
            
            if plot:
                plt.figure()
                plt.plot(self.tspan[1:],f(*args), 'r--', linewidth=5, label= 'imposed')
                plt.plot(self.tspan[1:],y['__s%d'%diff_eqs[k]], label='full')
                plt.legend(loc=0)
                plt.xlabel('time')
                plt.ylabel('population')
                if max(hey) < epsilon :
                    plt.title(str(self.model.species[diff_eqs[k]])+'passenger', fontsize=20)    
                else: plt.title(self.model.species[diff_eqs[k]], fontsize=20)   
                
            if max(hey) < epsilon : 
                self.passengers.append(diff_eqs[k])
                
#             s_points = sum(w < epsilon for w in hey)
#             if s_points > ptge_similar*len(hey) : 
#                 self.passengers.append(diff_eqs[k])
        plt.show()
        return self.passengers

    #This function finds conservation laws from the conserved cycles
    def mass_conserved(self, y, verbose=False):
        if(self.model.odes == None or self.model.odes == []):
            pysb.bng.generate_equations(self.model)
        h = [] # Array to hold conservation equation
        g = [] # Array to hold corresponding lists of free variables in conservation equations
        value_constants = {} #Dictionary that storage the value of each constant
        for i, item in enumerate(self.sto_conserved):
            b = 0
            u = 0
            for j, specie in enumerate(item):
                b += self.model.odes[self.sto_conserved[i][j]]
            if b == 0:
                g.append(item)
                for l,k in enumerate(item):
                    u += sympy.Symbol('__s%d' % self.sto_conserved[i][l])    
                h.append(u-sympy.Symbol('C%d'%i))
                if verbose: print '  cycle%d'%i, 'is conserved'
        
        for i in h:
            constant_to_solve = [atom for atom in i.atoms(sympy.Symbol) if re.match(r'[C]',str(atom))]
            solution = sympy.solve(i, constant_to_solve ,implicit = True)
            solution_ready = solution[0]
            for q in solution_ready.atoms(sympy.Symbol): solution_ready = solution_ready.subs(q, y[0][str(q)])
            value_constants[constant_to_solve[0]] = solution_ready
            
        (self.conservation, self.conserve_var, self.value_conservation) = h, g, value_constants     
        return h, g, value_constants

    def passenger_equations(self):
        if(self.model.odes == None or self.model.odes == []):
            pysb.bng.generate_equations(self.model)
        passenger_eqs = {}
        for i, j in enumerate(self.passengers):
            passenger_eqs[j] = self.model.odes[self.passengers[i]]
        return passenger_eqs

    def find_nearest_zero(self, array):
        idx = numpy.nanargmin(numpy.abs(array))
        return array[idx]

    # Make sure this is the "ignore:" y
    def pruned_equations(self, y, rho=1, ptge_similar=0.1):
        pruned_eqs = self.passenger_equations()
        equations  = copy.deepcopy(pruned_eqs)

        for j in equations:
            eq_monomials = equations[j].as_coefficients_dict().keys()   # Get monomials
            eq_monomials_iter = iter(eq_monomials)
            for l, m in enumerate(eq_monomials_iter):                        # Compares the monomials to find the pruned system
                m_ready = m                                             # Monomial to compute with
                m_elim  = m                                             # Monomial to save
                for p in self.param_values: m_ready = m_ready.subs(p, self.param_values[p]) # Substitute parameters
                second_mons_iter = iter(range(len(eq_monomials)))
                for k in second_mons_iter:
                    if (k+l+1) <= (len(eq_monomials)-1):
                        ble_ready = eq_monomials[k+l+1] # Monomial to compute with
                        ble_elim  = eq_monomials[k+l+1] # Monomial to save
                        for p in self.param_values: ble_ready = ble_ready.subs(p, self.param_values[p]) # Substitute parameters
                        args2 = []
                        args1 = []
                        variables_ble_ready = [atom for atom in ble_ready.atoms(sympy.Symbol) if not re.match(r'\d',str(atom))]
                        variables_m_ready = [atom for atom in m_ready.atoms(sympy.Symbol) if not re.match(r'\d',str(atom))]
                        f_ble = sympy.lambdify(variables_ble_ready, ble_ready, 'numpy' )
                        f_m = sympy.lambdify(variables_m_ready, m_ready, 'numpy' )
                        for uu,ll in enumerate(variables_ble_ready):
                            args2.append(y[:][str(ll)])
                        for w,s in enumerate(variables_m_ready):
                            args1.append(y[:][str(s)])
                        hey_pruned = numpy.log10(f_m(*args1)) - numpy.log10(f_ble(*args2))

                        closest = self.find_nearest_zero(hey_pruned)
                        if closest > 0 and closest > rho:
                            pruned_eqs[j] = pruned_eqs[j].subs(ble_elim, 0)
                        elif closest < 0 and closest < -rho:
                            pruned_eqs[j] = pruned_eqs[j].subs(m_elim, 0) 
                            break
                        
                        else:pass

        for i, l in enumerate(self.conservation): #Add the conservation laws to the pruned system
            pruned_eqs['cons%d'%i]=l
        self.pruned = pruned_eqs
        return pruned_eqs

    def solve_pruned(self):
        solve_for = copy.deepcopy(self.passengers)
        eqs       = copy.deepcopy(self.pruned)
        eqs_l = eqs.values()       
        
        for var_l in self.conserve_var:
            if len(var_l) == 1:
                solve_for.append(var_l[0])
        variables =  tuple(sympy.Symbol('__s%d' %var) for var in solve_for )
# Problem because there are more equations than variables
        sol = sympy.solve(eqs_l, variables, simplify=False, dict=False)
        print sol
        if isinstance(sol,dict):
            #TODO, ask Alex about this
            sol = [tuple(sol[v] for v in variables)]
#         sol=[]
        if len(sol) == 0:
            self.sol_pruned = { j:sympy.Symbol('__s%d'%j) for i, j in enumerate(solve_for) }
        else:
            self.sol_pruned = { j:sol[0][i] for i, j in enumerate(solve_for) }
                   
        return self.sol_pruned

    def equations_to_tropicalize(self):
        idx = list(set(range(len(self.model.odes))) - set(self.sol_pruned.keys()))
        eqs = { i:self.model.odes[i] for i in idx }

        for l in eqs.keys(): #Substitutes the values of the algebraic system
#             for k in self.sol_pruned.keys(): eqs[l]=eqs[l].subs(sympy.Symbol('s%d' % k), self.sol_pruned[k])
            for q in self.value_conservation.keys(): eqs[l] = eqs[l].subs(q, self.value_conservation[q])
#         for i in eqs.keys():
#             for par in self.model.parameters: eqs[i] = sympy.simplify(eqs[i].subs(par.name, par.value))
        self.eqs_for_tropicalization = eqs

        return eqs
    
    def final_tropicalization(self):
        tropicalized = {}
        
        for j in sorted(self.eqs_for_tropicalization.keys()):
            if type(self.eqs_for_tropicalization[j]) == sympy.Mul: tropicalized[j] = self.eqs_for_tropicalization[j] #If Mul=True there is only one monomial
            elif self.eqs_for_tropicalization[j] == 0: print 'there are no monomials'
            else:            
                ar = self.eqs_for_tropicalization[j].args #List of the terms of each equation  
                asd=0 
                for l, k in enumerate(ar):
                    p = k
                    for f, h in enumerate(ar):
                       if k != h:
                          p *= sympy.Heaviside(sympy.log(abs(k)) - sympy.log(abs(h)))
                    asd +=p
                tropicalized[j] = asd

        self.tropical_eqs = tropicalized
        return tropicalized

    def data_drivers(self, y):        
        tropical_system = self.final_tropicalization()
        trop_data = OrderedDict()
        signature_sp = {}

        for i in tropical_system.keys():
            signature = [0]*self.tspan[1:]
            mons_data = {}
            mons = sorted(tropical_system[i].as_coefficients_dict().items(),key=str)
            mons_matrix = numpy.zeros((len(mons),len(self.tspan[1:])), dtype=float)
            sign_monomial = tropical_system[i].as_coefficients_dict().values()
            for q, m_s in enumerate(mons):
                mon_inf = [None]*2
                j = list(m_s)
                jj = copy.deepcopy(j[0]) 
                for par in self.param_values: j[0]=j[0].subs(par, self.param_values[par])
                arg_f1 = []
                var_to_study = [atom for atom in j[0].atoms(sympy.Symbol) if not re.match(r'\d',str(atom))] #Variables of monomial 
                f1 = sympy.lambdify(var_to_study, j[0], modules = dict(Heaviside=_Heaviside_num, log=numpy.log10, Abs=numpy.abs)) 
                for va in var_to_study:
                   arg_f1.append(y[str(va)])    
                mon_inf[0]=f1(*arg_f1)
                mon_inf[1]=j[1]
                mons_data[str(jj).partition('*Heaviside')[0]] = mon_inf
                mons_matrix[q] = mon_inf[0]
            for col in range(len(self.tspan[1:])):
                signature[col] = numpy.nonzero(mons_matrix[:,col])[0][0]
            signature_sp[i] = signature
            trop_data[_parse_name(self.model.species[i])] = mons_data
        self.driver_signatures = signature_sp
        self.tro_species = trop_data
        return trop_data 
    
    def visualization(self, driver_specie=None):
        if driver_specie not in self.tro_species.keys():
            raise Exception("driver_specie is not driver")
        elif driver_specie in self.tro_species.keys():
            spec_ready = driver_specie
        
        step = 100
        monomials_dic = self.tro_species[spec_ready]
        colors = itertools.cycle(["b", "g", "c", "m", "y", "k" ])
        
        si_flux = 0
        no_flux = 0
        f = plt.figure(1)
        monomials = []
        for c, mon in enumerate(monomials_dic):
            x_concentration = numpy.nonzero(monomials_dic[mon][0])[0]
            if len(x_concentration) > 0:   
                monomials.append(mon)            
                si_flux+=1
                x_points = [self.tspan[x] for x in x_concentration] 
                prueba_y = numpy.repeat(2*si_flux, len(x_concentration))
                if monomials_dic[mon][1]==1 : plt.scatter(x_points[::int(len(self.tspan)/step)], prueba_y[::int(len(self.tspan)/step)], color = next(colors), marker=r'$\uparrow$', s=numpy.array([monomials_dic[mon][0][k] for k in x_concentration])[::int(len(self.tspan)/step)]*2)
                if monomials_dic[mon][1]==-1 : plt.scatter(x_points[::int(len(self.tspan)/step)], prueba_y[::int(len(self.tspan)/step)], color = next(colors), marker=r'$\downarrow$', s=numpy.array([monomials_dic[mon][0][k] for k in x_concentration])[::int(len(self.tspan)/step)]*2)
            else: no_flux+=1
        y_pos = numpy.arange(2,2*si_flux+4,2)    
        plt.yticks(y_pos, monomials, size = 'medium') 
        plt.xlabel('Time (s)')
        plt.ylabel('Monomials')
        plt.title('Tropicalization' + ' ' + spec_ready)
        plt.xlim(0, self.tspan[-1])
        plt.savefig('/home/carlos/Desktop/'+str(spec_ready), format='jpg', bbox_inches='tight', dpi=400)
        plt.show()


#         plt.ylim(0, len(monomials)+1) 
        return f  

    def get_trop_data(self):
        return self.tro_species.keys()
    def get_passenger(self):
        return self.passengers
    def get_pruned_equations(self):
        return self.pruned
    
def run_tropical(model, tspan, parameters = None, sp_visualize = None):
    tr = Tropical(model)
    tr.tropicalize(tspan, parameters)
    if sp_visualize is not None:
        tr.visualization(driver_specie=sp_visualize)
    return tr.get_pruned_equations(),tr.get_passenger()


 
