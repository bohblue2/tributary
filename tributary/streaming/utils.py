import asyncio
import json as JSON
import os
from .node import Node
from ..base import StreamNone, StreamRepeat, StreamEnd, TributaryException


def Delay(node, delay=1):
    """Streaming wrapper to delay a stream

    Arguments:
        node (node): input stream
        delay (float): time to delay input stream
    """

    async def foo(val):
        await asyncio.sleep(delay)
        return val

    ret = Node(foo=foo, name="Delay", inputs=1)
    node >> ret
    return ret


# class State(Node):
#     '''Streaming wrapper to delay a stream

#     Arguments:
#         node (node): input stream
#         state (dict): state dictionary of values to hold
#     '''

#     def __init__(self, node, delay=1):
#         async def foo(val):
#             await asyncio.sleep(delay)
#             return val

#         super().__init__(foo=foo, foo_kwargs=None, name='Delay', inputs=1)
#         node.downstream().append((self, 0))
#         self.upstream().append(node)


def Apply(node, foo, foo_kwargs=None):
    """Streaming wrapper to apply a function to an input stream

    Arguments:
        node (node): input stream
        foo (callable): function to apply
        foo_kwargs (dict): kwargs for function
    """

    def _foo(val):
        return ret._apply(val, **ret._apply_kwargs)

    ret = Node(foo=_foo, name="Apply", inputs=1)
    ret.set("_apply", foo)
    ret.set("_apply_kwargs", foo_kwargs or {})
    node >> ret
    return ret


def Window(node, size=-1, full_only=False):
    """Streaming wrapper to collect a window of values

    Arguments:
        node (node): input stream
        size (int): size of windows to use
        full_only (bool): only return if list is full
    """

    def foo(val, size=size, full_only=full_only):
        if size == 0:
            return val
        else:
            ret._accum.append(val)

        if size > 0:
            ret._accum = ret._accum[-size:]

        if full_only and len(ret._accum) == size:
            return ret._accum
        elif full_only:
            return StreamNone()
        else:
            return ret._accum

    ret = Node(foo=foo, name="Window[{}]".format(size if size > 0 else "∞"), inputs=1)
    ret.set("_accum", [])
    node >> ret
    return ret


def Unroll(node):
    """Streaming wrapper to unroll an iterable stream. Similar to Curve

    Arguments:
        node (node): input stream
    """

    async def foo(value):
        # unrolled
        if ret._count > 0:
            ret._count -= 1
            return value

        # unrolling
        try:
            for v in value:
                ret._count += 1
                await ret._push(v, 0)
        except TypeError:
            return value
        else:
            return StreamRepeat()

    ret = Node(foo=foo, name="Unroll", inputs=1)
    ret.set("_count", 0)
    node >> ret
    return ret


def UnrollDataFrame(node, json=False, wrap=False):
    """Streaming wrapper to unroll a dataframe into a stream

    Arguments:
        node (node): input stream
    """

    async def foo(value, json=json, wrap=wrap):
        # unrolled
        if ret._count > 0:
            ret._count -= 1
            return value

        # unrolling
        try:
            for i in range(len(value)):
                row = value.iloc[i]

                if json:
                    data = row.to_dict()
                    data["index"] = row.name
                else:
                    data = row
                ret._count += 1
                await ret._push(data, 0)

        except TypeError:
            return value
        else:
            return StreamRepeat()

    ret = Node(foo=foo, name="UnrollDF", inputs=1)
    ret.set("_count", 0)
    node >> ret
    return ret


def Merge(node1, node2):
    """Streaming wrapper to merge 2 inputs into a single output

    Arguments:
        node1 (node): input stream
        node2 (node): input stream
    """

    def foo(value1, value2):
        return value1, value2

    ret = Node(foo=foo, name="Merge", inputs=2)
    node1 >> ret
    node2 >> ret
    return ret


def ListMerge(node1, node2):
    """Streaming wrapper to merge 2 input lists into a single output list

    Arguments:
        node1 (node): input stream
        node2 (node): input stream
    """

    def foo(value1, value2):
        return list(value1) + list(value2)

    ret = Node(foo=foo, name="ListMerge", inputs=2)
    node1 >> ret
    node2 >> ret
    return ret


def DictMerge(node1, node2):
    """Streaming wrapper to merge 2 input dicts into a single output dict.
       Preference is given to the second input (e.g. if keys overlap)

    Arguments:
        node1 (node): input stream
        node2 (node): input stream
    """

    def foo(value1, value2):
        ret = {}
        ret.update(value1)
        ret.update(value2)
        return ret

    ret = Node(foo=foo, name="DictMerge", inputs=2)
    node1 >> ret
    node2 >> ret
    return ret


def FixedMap(node, count, mapper=None):
    """Streaming wrapper to split stream into a fixed number of outputs

    Arguments:
        node (Node): input stream
        count (int): number of output nodes to generate
        mapper (function): how to map the inputs into `count` streams
    """
    rets = []

    def _default_mapper(value, i):
        return value[i]

    for _ in range(count):

        def foo(value, i=_, mapper=mapper or _default_mapper):
            return mapper(value, i)

        ret = Node(foo=foo, name="FixedMap", inputs=1)
        node >> ret
        rets.append(ret)

    return rets


def Reduce(*nodes, reducer=None):
    """Streaming wrapper to merge any number of inputs

    Arguments:
        nodes (tuple): input streams
        reducer (function): how to map the outputs into one stream
    """

    def foo(*values, reducer=reducer):
        return values if reducer is None else reducer(*values)

    ret = Node(foo=foo, name="Reduce", inputs=len(nodes))
    for i, n in enumerate(nodes):
        n >> ret
    return ret


def Subprocess(
    node, command, json=False, std_err=False, one_off=False, node_to_command=False
):
    """Open up a subprocess and yield the results as they come

    Args:
        node (Node): input stream
        command (str): command to run
        std_err (bool): include std_err
    """
    if node_to_command and not one_off:
        raise TributaryException("Piping upstream values to command assumes one off")

    async def _proc(value, command=command, std_err=std_err, one_off=one_off):
        if ret._proc is None:
            if node_to_command:
                command = command.format(value)

            proc = await asyncio.create_subprocess_shell(
                command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            ret._proc = proc

        if one_off:
            stdout, stderr = await proc.communicate()

            if stdout:
                stdout = stdout.decode()
            if stderr:
                stderr = stderr.decode()

            ret._proc = None

            if std_err:
                return stdout, stderr
            else:
                return stdout

        else:
            if value == StreamEnd():
                try:
                    ret._proc.terminate()
                    ret._proc.kill()
                    os.kill(ret._proc.pid)
                except ProcessLookupError:
                    pass

                await ret._proc.wait()
                ret._proc = None

            if json:
                value = JSON.dumps(value)

            ret._proc.stdin.write("{}\n".format(value).encode("utf8"))
            await ret._proc.stdin.drain()

            val = await asyncio.create_task(ret._proc.stdout.readline())
            val = val.decode().strip()

            if val == "":
                await ret._proc.wait()
                ret._proc = None
                return StreamEnd()

            if json:
                val = JSON.loads(val)
            return val

    ret = Node(foo=_proc, name="Proc", inputs=1)
    ret.set("_proc", None)
    node >> ret
    return ret


Node.delay = Delay
# Node.state = State
Node.apply = Apply
Node.window = Window
Node.unroll = Unroll
Node.unrollDataFrame = UnrollDataFrame
Node.merge = Merge
Node.listMerge = ListMerge
Node.dictMerge = DictMerge
Node.map = FixedMap
Node.reduce = Reduce
Node.proc = Subprocess
