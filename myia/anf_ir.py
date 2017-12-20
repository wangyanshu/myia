"""Intermediate representation definition.

Myia's main intermediate representation (IR) is a graph-based version of ANF.
Each function definition (lambda) is defined as a graph, consisting of a series
of function applications.

A function can be applied to a node from another funtion's graph; this
implicitly creates a nested function. Functions are first-class objects, so
returning a nested function creates a closure.

"""
from typing import (List, Set, Tuple, Dict, Any, Sequence, MutableSequence,
                    overload, Iterable)

from myia.ir import Node
from myia.utils import Named

PARAMETER = Named('PARAMETER')
RETURN = Named('RETURN')


class Graph:
    """A function graph.

    Attributes:
        parameters: The parameters of this function as a list of `Parameter`
            nodes. Parameter nodes that are unreachable by walking from the
            output node correspond to unused parameters.
        return_: The `Return` node whose value will be returned by this
            function. A graph initially has no output node (because it won't be
            known e.g. until the function has completed parsing), but it must
            be set afterwards for the graph instance to be valid.

    """

    def __init__(self) -> None:
        """Construct a graph."""
        self.parameters: List[Parameter] = []
        self.return_: Return = None
        self.debug: Dict = {}


class ANFNode(Node):
    """A node in the graph-based ANF IR.

    There are four types of nodes: Function applications; parameters; return
    values; and constants such as numbers and functions.

    Attributes:
        inputs: If the node is a function application, the first node input is
            the function to apply, followed by the arguments. These are use-def
            edges.
        value: The value of this node, if it is a constant. Parameters have the
            special value `PARAMETER`.
        graph: The function definition graph that this node belongs to for
            values and parameters.
        uses: A set of tuples with the nodes that use this node alongside with
            the index. These def-use edges are the reverse of the `inputs`
            attribute, creating a doubly linked graph structure.
        debug: A dictionary with debug information about this node e.g. a
            human-readable name and the Python source code.

    """

    def __init__(self, inputs: Iterable['ANFNode'], value: Any,
                 graph: Graph) -> None:
        """Construct a node."""
        self._inputs = Inputs(self, inputs)
        self.value = value
        self.graph = graph
        self.uses: Set[Tuple[ANFNode, int]] = set()
        self.debug: Dict = {}

    @property
    def inputs(self) -> 'Inputs':
        """Return the list of inputs."""
        return self._inputs

    @inputs.setter
    def inputs(self, value: Iterable['ANFNode']) -> None:
        """Set the list of inputs."""
        self._inputs.clear()  # type: ignore
        self._inputs = Inputs(self, value)

    @property
    def incoming(self):
        return iter(self.inputs)

    @property
    def outgoing(self):
        return (node for node, index in self.uses)

    def __copy__(self):
        cls = self.__class__
        obj = cls.__new__(cls)
        Node.__init__(obj, self.inputs, self.value, self.graph)
        return obj

    def replace(self, other: Node) -> None:
        """Replace one node in the graph with another.

        Args:
            other: The node to replace this one with. All incoming and outgoing
                edges will be replace.

        """
        other.inputs = self.inputs
        self.inputs.clear()  # type: ignore
        for node, index in self.uses:
            node.inputs[index] = other


class Inputs(MutableSequence[ANFNode]):
    """Container data structure for node inputs.

    This mutable sequence data structure can be used to keep track of a node's
    inputs. Any insertion or deletion of edges will be reflected in the inputs'
    `uses` attribute.

    """

    def __init__(self, node: ANFNode,
                 initlist: Iterable[ANFNode] = None) -> None:
        """Construct the inputs container for a node.

        Args:
            node: The node of which the inputs are stored.
            initlist: A sequence of nodes to initialize the container with.

        """
        self.node = node
        self.data: List[ANFNode] = []
        if initlist is not None:
            self.extend(initlist)

    @overload
    def __getitem__(self, index: int) -> ANFNode:
        pass

    @overload  # noqa: F811
    def __getitem__(self, index: slice) -> Sequence[ANFNode]:
        pass

    def __getitem__(self, index):  # noqa: F811
        """Get an input by its index."""
        return self.data[index]

    @overload
    def __setitem__(self, index: int, value: ANFNode) -> None:
        pass

    @overload  # noqa: F811
    def __setitem__(self, index: slice, value: Iterable[ANFNode]) -> None:
        pass

    def __setitem__(self, index, value):  # noqa: F811
        """Replace an input with another."""
        if isinstance(index, slice):
            raise ValueError("slice assignment not supported")
        if index < 0:
            index += len(self)
        old_value = self.data[index]
        old_value.uses.remove((self.node, index))
        value.uses.add((self.node, index))
        self.data[index] = value

    @overload
    def __delitem__(self, index: int) -> None:
        pass

    @overload  # noqa: F811
    def __delitem__(self, index: slice) -> None:
        pass

    def __delitem__(self, index):  # noqa: F811
        """Delete an input."""
        if isinstance(index, slice):
            raise ValueError("slice deletion not supported")
        if index < 0:
            index += len(self)
        value = self.data[index]
        value.uses.remove((self.node, index))
        for i, next_value in enumerate(self.data[index + 1:]):
            next_value.uses.remove((self.node, i + index + 1))
            next_value.uses.add((self.node, i + index))
        del self.data[index]

    def __len__(self) -> int:
        """Get the number of inputs."""
        return len(self.data)

    def insert(self, index: int, value: ANFNode) -> None:
        """Insert an input at a given location."""
        if index < 0:
            index += len(self)
        for i, next_value in enumerate(reversed(self.data[index:])):
            next_value.uses.remove((self.node, len(self) - i - 1))
            next_value.uses.add((self.node, len(self) - i))
        value.uses.add((self.node, index))
        self.data.insert(index, value)

    def __repr__(self) -> str:
        """Return a string representation of the inputs."""
        return f"Inputs({self.data})"


class Apply(ANFNode):
    """A function application.

    This node represents the application of a function to a set of arguments.

    """

    def __init__(self, inputs: List[ANFNode], graph: 'Graph') -> None:
        """Construct an application."""
        super().__init__(inputs, None, graph)


class Parameter(ANFNode):
    """A parameter to a function.

    Parameters are leaf nodes, since they are not the result of a function
    application, and they have no value. They are entirely defined by the graph
    they belong to.

    """

    def __init__(self, graph: Graph) -> None:
        """Construct the parameter."""
        super().__init__([], PARAMETER, graph)


class Return(ANFNode):
    """The value returned by a function.

    Return nodes have exactly one input, which points to the value that the
    function will return. They are a root node in the function graph.

    """

    def __init__(self, input_: ANFNode, graph: Graph) -> None:
        """Construct a return node."""
        super().__init__([input_], RETURN, graph)


class Constant(ANFNode):
    """A constant node.

    A constant is a node which is not the result of a function application. In
    the graph it is a leaf node. It has no inputs, and instead is defined
    entirely by its value. Unlike parameters and values, constants do not
    belong to any particular function graph.

    Two "special" constants are those whose value is a `Primitive`
    (representing primitive operations) or whose value is a `Graph` instance
    (representing functions).

    """

    def __init__(self, value: Any) -> None:
        """Construct a literal."""
        super().__init__([], value, None)