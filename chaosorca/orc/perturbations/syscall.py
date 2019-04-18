from functools import reduce

# Package import
import docker

# Local imports
import config
from misc import container_api
from misc import common_helpers as common
from monitoring import monitoring
import monitoring.prometheus_targets as monitoring_targets

# Strace documentation
# http://man7.org/linux/man-pages/man1/strace.1.html
#  [:error=errno:retval=value]
#  [:signal=sig]
#  [:syscall=syscall]
#  [:delay_enter=usecs]
#  [:delay_exit=usecs]
#  [:when=expr]

# Local classes to handle syscall fault definitions.

class FaultObject:
    name = ''
    value = ''

    def __init__(self, name, value):
        self.name = name
        self.value = value

    def __str__(self):
        '''Returns the representation %s=%s'''
        return '%s=%s' % (self.name, self.value)

class Fault:
    def __init__(self,
        delay_enter=None,
        delay_exit=None,
        error=None,
        signal=None,
        syscall=None,
        when=None):

        # Initalise delay with *1000 to use milliseconds instead of microseconds.
        self.delay_enter = FaultObject('delay_enter', delay_enter*1000 if delay_enter else None)
        self.delay_exit = FaultObject('delay_exit', delay_exit*1000 if delay_exit else None)
        self.error = FaultObject('error', error)
        self.signal = FaultObject('signal', signal)
        self.syscall = FaultObject('syscall', syscall)
        self.when = FaultObject('when', when)

    def __str__(self):
        '''Returns a string represenation of the fault as required by Strace.'''
        # First filter out FaultObjects containing None.
        # Second convert FaultObject to their str represenation
        # Thirdly join the elements together with ':' between them.
        cmds = ':'.join(map(str, filter(
            lambda elem : elem.value is not None,
                        [self.delay_enter,
                        self.delay_exit,
                        self.error,
                        self.signal,
                        self.when])))
        # Syscall should be the first argument and without the equals part.
        return '%s:%s' % (self.syscall.value, cmds)

# Variables
docker_client = docker.from_env()

def applyFault(container, fault):
    '''Appplies the given fault to the given container'''

    # Handle already running fault injection containers.
    try:
        testc = docker_client.containers.get(config.SYSC_NAME+'.'+container.name)
        # Fault container already running, remove it first.
        clearFaults(container)
    except Exception:
        pass

    # Get local PID's
    processes = container_api.getProcesses(container.name)['processes']

    if len(processes) == 1:
        # Easy case, just select that one for monitoring.
        pid_to_perturb = processes[0][0] # First process, where the first value is the PID.
    elif len(processes) > 1:
        # Harder case, ask to select one.
        print('Multiple processes to choose from, please select 1.')
        print('\n'.join(["PID:%s %s" % (proc[0], proc[-1]) for proc in processes]))
        pid_to_perturb = input('Input PID to perturb: ')

    sysc_container = docker_client.containers.run(
        'chaosorca/sysc',
        cap_add=['SYS_PTRACE'],
        detach=True,
        environment=['SYSC_PID='+pid_to_perturb, 'SYSC_FAULT='+str(fault)],
        name=config.SYSC_NAME+'.'+container.name,
        pid_mode="container:"+container.name,
        remove=True)

    docker_client.networks.get(config.MONITORING_NETWORK_NAME).connect(sysc_container.name)
    sysc_container.reload() # Refresh container variable with new IPAddress content.
    sysc_container_monit_ip = common.getIpFromContainerAttachedNetwork(sysc_container, config.MONITORING_NETWORK_NAME)

    monitoring_targets.add(sysc_container_monit_ip +':'+ config.MONITORING_DEFAULT_PORT, container.name+'.c')
    return sysc_container

def clearFaults(container):
    '''Removes any fault injection'''
    try:
        sysc_container = docker_client.containers.get(config.SYSC_NAME+'.'+container.name)
    except Exception:
        print('No syscall fault injection currently running')
        exit()

    #Stop perturbation container.
    monitoring_targets.remove(container.name+'.c')
    return sysc_container.stop()
